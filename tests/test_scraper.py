"""Unit tests for driver creation (no browser is launched)."""

import pytest
from selenium.common.exceptions import WebDriverException

from crypto_tracker import config, scraper

DRIVER_PATH = r"C:\fake\wdm\chromedriver.exe"


class FakeDriver:
    """Stands in for webdriver.Chrome; records what it was built with."""

    def __init__(self, service=None, options=None):
        self.service = service
        self.options = options
        self.page_load_timeout = None

    def set_page_load_timeout(self, seconds):
        self.page_load_timeout = seconds


def application_control_error():
    """The exact OSError Windows raises when WDAC blocks a binary."""
    exc = OSError("An Application Control policy has blocked this file")
    exc.winerror = scraper.APPLICATION_CONTROL_WINERROR
    return exc


@pytest.fixture(autouse=True)
def no_network(monkeypatch):
    """Neither DNS resolution nor a real browser may be touched."""
    monkeypatch.setattr(scraper.dns_resolver, "resolve_host", lambda host=None: None)


@pytest.fixture()
def wdm_installs(monkeypatch):
    monkeypatch.setattr(
        scraper.ChromeDriverManager, "install", lambda self: DRIVER_PATH
    )


@pytest.fixture()
def calls(monkeypatch):
    """Record every webdriver.Chrome construction, failing per a scripted map."""
    recorded = []

    def factory(script):
        def _chrome(service=None, options=None):
            recorded.append({"service": service, "options": options})
            outcome = script[len(recorded) - 1]
            if isinstance(outcome, BaseException):
                raise outcome
            return outcome

        return _chrome

    return recorded, factory


def install_chrome(monkeypatch, recorded, factory, script):
    monkeypatch.setattr(scraper.webdriver, "Chrome", factory(script))
    return recorded


def test_primary_path_uses_webdriver_manager_without_fallback(
    monkeypatch, wdm_installs, calls
):
    """The happy path builds the driver from the webdriver_manager path."""
    recorded, factory = calls
    install_chrome(monkeypatch, recorded, factory, [FakeDriver()])

    driver = scraper.create_driver(headless=True)

    assert len(recorded) == 1, "no fallback attempt on the happy path"
    service = recorded[0]["service"]
    assert service is not None
    assert service.path == DRIVER_PATH
    assert driver.page_load_timeout == config.PAGE_LOAD_TIMEOUT


def test_application_control_block_falls_back_to_selenium(
    monkeypatch, wdm_installs, calls
):
    """WinError 4551 is the one condition that permits the fallback."""
    recorded, factory = calls
    install_chrome(
        monkeypatch,
        recorded,
        factory,
        [application_control_error(), FakeDriver()],
    )

    driver = scraper.create_driver(headless=True)

    assert len(recorded) == 2, "fallback must be attempted once"
    assert recorded[0]["service"] is not None
    assert recorded[0]["service"].path == DRIVER_PATH
    assert recorded[1]["service"] is None, "fallback uses Selenium's own resolution"
    assert driver.page_load_timeout == config.PAGE_LOAD_TIMEOUT


def test_fallback_still_builds_the_same_options(monkeypatch, wdm_installs, calls):
    """Headless and the pinned host survive the fallback."""
    monkeypatch.setattr(
        scraper.dns_resolver, "resolve_host", lambda host=None: "1.2.3.4"
    )
    recorded, factory = calls
    install_chrome(
        monkeypatch,
        recorded,
        factory,
        [application_control_error(), FakeDriver()],
    )

    scraper.create_driver(headless=True)

    for entry in recorded:
        arguments = entry["options"].arguments
        assert "--headless=new" in arguments
        assert any(
            arg.startswith("--host-resolver-rules=MAP") for arg in arguments
        )


def test_other_oserror_does_not_fall_back(monkeypatch, wdm_installs, calls):
    """An unrelated OSError must propagate instead of silently retrying."""
    other = OSError("access denied")
    other.winerror = 5

    recorded, factory = calls
    install_chrome(monkeypatch, recorded, factory, [other])

    with pytest.raises(OSError):
        scraper.create_driver(headless=True)

    assert len(recorded) == 1, "must not retry for a non-4551 error"


def test_webdriver_exception_does_not_fall_back(monkeypatch, wdm_installs, calls):
    """Requirement: never fall back for every WebDriverException."""
    recorded, factory = calls
    install_chrome(
        monkeypatch,
        recorded,
        factory,
        [WebDriverException("session not created")],
    )

    with pytest.raises(scraper.ScraperError):
        scraper.create_driver(headless=True)

    assert len(recorded) == 1, "must not retry for a WebDriverException"


def test_scraper_error_when_fallback_also_fails(monkeypatch, wdm_installs, calls):
    """If the fallback driver also fails, report a single clear error."""
    recorded, factory = calls
    install_chrome(
        monkeypatch,
        recorded,
        factory,
        [application_control_error(), WebDriverException("also broken")],
    )

    with pytest.raises(scraper.ScraperError, match="Selenium's own driver"):
        scraper.create_driver(headless=True)

    assert len(recorded) == 2


def test_driver_download_failure_raises_scraper_error(monkeypatch, calls):
    """A webdriver_manager lookup failure is reported, never falls back."""

    def boom(self):
        raise Exception("no driver found")

    monkeypatch.setattr(scraper.ChromeDriverManager, "install", boom)

    recorded, factory = calls
    install_chrome(monkeypatch, recorded, factory, [])

    with pytest.raises(scraper.ScraperError, match="webdriver_manager"):
        scraper.create_driver(headless=True)

    assert recorded == [], "must not launch Chrome without a driver"


def test_is_application_control_block_is_narrow():
    assert scraper._is_application_control_block(application_control_error()) is True

    plain = OSError("something else")
    assert scraper._is_application_control_block(plain) is False

    other = OSError("access denied")
    other.winerror = 5
    assert scraper._is_application_control_block(other) is False

    assert scraper._is_application_control_block(ValueError("nope")) is False
    assert scraper._is_application_control_block(
        WebDriverException("nope")
    ) is False
