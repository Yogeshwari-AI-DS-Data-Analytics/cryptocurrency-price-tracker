"""Selenium layer: drives Chrome and returns raw table text.

Nothing in this module parses numbers or touches the filesystem; it only reads
what is on screen.
"""

import logging
from typing import Any, Dict, List, Optional

from selenium import webdriver
from selenium.common.exceptions import (
    NoSuchElementException,
    TimeoutException,
    WebDriverException,
)
from selenium.webdriver.chrome.options import Options
from selenium.webdriver.chrome.service import Service
from selenium.webdriver.common.by import By
from selenium.webdriver.support import expected_conditions as EC
from selenium.webdriver.support.ui import WebDriverWait
from webdriver_manager.chrome import ChromeDriverManager

from . import config, dns_resolver

logger = logging.getLogger(__name__)

# Windows Application Control refuses to execute a blocked binary with this
# winerror. It is the only condition that justifies the driver fallback below.
APPLICATION_CONTROL_WINERROR = 4551


def _is_application_control_block(exc: BaseException) -> bool:
    """True only for the Windows Application Control execution block.

    Deliberately narrow: no other OSError, and no WebDriverException, may
    trigger the fallback, so genuine setup failures are still reported.
    """
    return (
        isinstance(exc, OSError)
        and getattr(exc, "winerror", None) == APPLICATION_CONTROL_WINERROR
    )


class ScraperError(Exception):
    """Raised when the page cannot be loaded or the table cannot be read."""


def build_chrome_options(
    headless: bool, host_ip: Optional[str] = None
) -> Options:
    """Create Chrome options, enabling headless mode when requested.

    Args:
        headless: run Chrome without a visible window.
        host_ip: address to use for the target host instead of the system
            DNS answer. Chrome still sends ``coinmarketcap.com`` as the TLS
            server name, so certificates are validated against the real
            hostname; only the lookup is redirected.
    """
    options = Options()

    if headless:
        options.add_argument("--headless=new")
        options.add_argument("--disable-gpu")

    if host_ip:
        options.add_argument(
            f"--host-resolver-rules=MAP {config.TARGET_HOST} {host_ip}"
        )
        logger.info(
            "Pinned %s to %s for this run", config.TARGET_HOST, host_ip
        )

    options.add_argument(f"--window-size={config.CHROME_WINDOW_SIZE}")
    options.add_argument(f"--user-agent={config.USER_AGENT}")
    options.add_argument("--lang=en-US")
    options.add_argument("--disable-blink-features=AutomationControlled")
    options.add_argument("--no-sandbox")
    options.add_argument("--disable-dev-shm-usage")
    options.add_argument("--disable-search-engine-choice-screen")
    options.add_experimental_option("excludeSwitches", ["enable-automation"])
    options.add_experimental_option("useAutomationExtension", False)
    options.set_capability("pageLoadStrategy", "eager")

    return options


def create_driver(headless: bool) -> webdriver.Chrome:
    """Launch Chrome, providing chromedriver through webdriver_manager.

    webdriver_manager is the primary driver provider. The single exception is
    Windows Application Control (winerror 4551) blocking execution of the
    driver it downloaded; only then does this fall back to Selenium's own
    driver resolution, so scraping keeps working on locked-down machines.

    The target host is resolved first: if the system DNS answer is unusable,
    a verified address is pinned via --host-resolver-rules for this run only.
    """
    host_ip = dns_resolver.resolve_host()

    try:
        driver_binary = ChromeDriverManager().install()
    except Exception as exc:  # noqa: BLE001 - webdriver_manager raises bare
        # Exception on a failed lookup/download, with no dedicated type.
        raise ScraperError(
            "Could not obtain chromedriver via webdriver_manager. Check that "
            "Google Chrome is installed and that the driver can be "
            "downloaded.\n"
            f"Original error: {exc}"
        ) from exc

    logger.debug("chromedriver resolved to %s", driver_binary)

    try:
        driver = webdriver.Chrome(
            service=Service(executable_path=driver_binary),
            options=build_chrome_options(headless, host_ip=host_ip),
        )
    except OSError as exc:
        if not _is_application_control_block(exc):
            raise

        logger.warning(
            "Windows Application Control blocked execution of the "
            "webdriver_manager driver (%s). Falling back to Selenium's own "
            "driver resolution for this run.",
            driver_binary,
        )
        try:
            driver = webdriver.Chrome(
                options=build_chrome_options(headless, host_ip=host_ip)
            )
        except WebDriverException as fallback_exc:
            raise ScraperError(
                "Could not start Chrome with either the webdriver_manager "
                "driver or Selenium's own driver resolution.\n"
                f"Original error: {fallback_exc}"
            ) from fallback_exc
    except WebDriverException as exc:
        raise ScraperError(
            "Could not start Chrome. Make sure Google Chrome is installed and "
            "that chromedriver matches its version.\n"
            f"Original error: {exc}"
        ) from exc

    driver.set_page_load_timeout(config.PAGE_LOAD_TIMEOUT)
    logger.info("Chrome started (headless=%s)", headless)
    return driver


def dismiss_banner(driver: webdriver.Chrome) -> None:
    """Close a cookie / consent banner if one is covering the table."""
    for selector in config.COOKIE_BANNER_SELECTORS:
        try:
            button = WebDriverWait(driver, 2).until(
                EC.element_to_be_clickable((By.CSS_SELECTOR, selector))
            )
            button.click()
            logger.info("Dismissed overlay via %s", selector)
            return
        except (TimeoutException, WebDriverException, NoSuchElementException):
            continue
    logger.debug("No cookie banner needed dismissing")


def _locate_table(driver: webdriver.Chrome) -> Any:
    """Return the table element that contains coin rows."""
    for selector in config.TABLE_CANDIDATES:
        tables = driver.find_elements(By.CSS_SELECTOR, selector)
        for table in tables:
            if table.find_elements(By.CSS_SELECTOR, config.TABLE_ROW_SELECTOR):
                logger.info("Matched table with selector %s", selector)
                return table
    raise ScraperError(
        "No cryptocurrency table found. CoinMarketCap may have changed its "
        "markup or served a bot-protection challenge page."
    )


def _scroll_to_load_rows(driver: webdriver.Chrome, target: int) -> int:
    """Scroll in steps so lazily rendered rows are attached to the DOM."""
    stable_passes = 0
    previous_count = 0

    for _ in range(12):
        driver.execute_script(
            "window.scrollTo(0, document.body.scrollHeight);"
        )
        current = len(
            driver.find_elements(By.CSS_SELECTOR, config.TABLE_ROW_SELECTOR)
        )
        logger.debug("Scroll pass: %s rows in DOM", current)

        if current >= target:
            return current
        if current == previous_count:
            stable_passes += 1
            if stable_passes >= 2:
                break
        else:
            stable_passes = 0
        previous_count = current

    return len(driver.find_elements(By.CSS_SELECTOR, config.TABLE_ROW_SELECTOR))


def _column_map(table: Any) -> Dict[str, int]:
    """Map field name -> column index using the header labels.

    Indexing by header keeps working when the website inserts or removes a
    column; positional indexing would silently shift.
    """
    headers = [h.text.strip().lower() for h in table.find_elements(
        By.CSS_SELECTOR, config.TABLE_HEADER_SELECTOR
    )]

    mapping: Dict[str, int] = {}
    for field, keywords in config.HEADER_KEYWORDS.items():
        for index, header in enumerate(headers):
            if any(keyword in header for keyword in keywords):
                mapping[field] = index
                break

    logger.debug("Header map: %s (headers=%s)", mapping, headers)
    return mapping


def _cell_text(row: Any, index: Optional[int]) -> Optional[str]:
    """Read one cell by index, never raising on short rows."""
    if index is None:
        return None
    cells = row.find_elements(By.CSS_SELECTOR, "td")
    if index >= len(cells):
        return None
    try:
        return cells[index].text
    except WebDriverException:
        return None


def _extract_name_symbol(row: Any, columns: Dict[str, int]) -> tuple:
    """Prefer dedicated name/symbol elements, fall back to the name cell."""
    name_index = columns.get("name")

    def first_text(selectors) -> Optional[str]:
        for selector in selectors:
            try:
                element = row.find_element(By.CSS_SELECTOR, selector)
                if element.text.strip():
                    return element.text
            except (NoSuchElementException, WebDriverException):
                continue
        return None

    name = first_text(config.COIN_NAME_SELECTORS)
    symbol = first_text(config.COIN_SYMBOL_SELECTORS)

    if name is None:
        raw = _cell_text(row, name_index) or ""
        parts = [part.strip() for part in raw.splitlines() if part.strip()]
        name = parts[0] if parts else None
        if symbol is None and len(parts) > 1:
            symbol = parts[1]

    return name, symbol


def _row_texts(row: Any, columns: Dict[str, int]) -> Dict[str, Optional[str]]:
    """Collect the raw text for one table row."""
    name, symbol = _extract_name_symbol(row, columns)
    return {
        "rank": _cell_text(row, columns.get("rank")),
        "name": name,
        "symbol": symbol,
        "price": _cell_text(row, columns.get("price")),
        "change_24h": _cell_text(row, columns.get("change_24h")),
        "market_cap": _cell_text(row, columns.get("market_cap")),
    }


def scrape_top_coins(headless: bool = True) -> List[Dict[str, Optional[str]]]:
    """Return raw cell text for the rows needed to build the Top 10.

    ``config.COIN_COUNT`` plus ``config.SCRAPE_ROW_SLACK`` rows are collected
    so that non-coin entries at the top of the table cannot consume Top 10
    slots; the cut itself happens after filtering, in
    dataframe.build_dataframe().

    Args:
        headless: run Chrome without a visible window.

    Raises:
        ScraperError: if the site cannot be reached at all, the table is
            missing, or no coin rows could be read. A page-load timeout is
            not an error: the partially loaded page is still scanned.
    """
    driver = create_driver(headless)

    try:
        try:
            driver.get(config.TARGET_URL)
        except TimeoutException:
            # A slow page (blocked images/fonts/analytics) never fires the
            # load event, but the document and its table are usually already
            # rendered. Keep going and let the table detection below decide.
            logger.warning(
                "Page load timed out after %ss; continuing because the "
                "cryptocurrency table may already be rendered.",
                config.PAGE_LOAD_TIMEOUT,
            )
        except WebDriverException as exc:
            raise ScraperError(
                f"Could not reach {config.TARGET_URL}: {exc}"
            ) from exc

        try:
            WebDriverWait(driver, config.ELEMENT_TIMEOUT).until(
                EC.presence_of_element_located(
                    (By.CSS_SELECTOR, config.TABLE_ROW_SELECTOR)
                )
            )
        except TimeoutException as exc:
            raise ScraperError(
                "Coin rows never appeared. The page may be blocked by bot "
                "protection or the markup changed."
            ) from exc

        dismiss_banner(driver)

        table = _locate_table(driver)
        _scroll_to_load_rows(driver, config.COIN_COUNT + config.SCRAPE_ROW_SLACK)
        columns = _column_map(table)

        if "price" not in columns:
            raise ScraperError(
                "Could not identify the price column from the table headers."
            )

        # Re-read after scrolling: rows may have been re-rendered.
        rows = driver.find_elements(By.CSS_SELECTOR, config.TABLE_ROW_SELECTOR)

        collected: List[Dict[str, Optional[str]]] = []
        for row in rows[: config.COIN_COUNT + config.SCRAPE_ROW_SLACK]:
            try:
                texts = _row_texts(row, columns)
            except WebDriverException:
                # StaleElementReferenceException and friends: skip and retry
                # the next row rather than failing the whole run.
                logger.warning("Skipped an unreadable table row")
                continue
            if texts.get("price"):
                collected.append(texts)

        if not collected:
            raise ScraperError(
                "No coin rows could be read from the table."
            )

        logger.info("Read %s raw rows from the page", len(collected))

        # Not truncated to COIN_COUNT here: the leading rows can include
        # non-coin entries (index products, ad slots) that the parser drops,
        # and cutting now would cost a real coin its place. The Top 10 cut is
        # applied after filtering, in dataframe.build_dataframe().
        return collected

    finally:
        try:
            driver.quit()
            logger.info("Chrome closed")
        except WebDriverException:
            logger.warning("Chrome did not shut down cleanly")