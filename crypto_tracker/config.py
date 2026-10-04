"""Central configuration: URLs, CSS selectors, paths and column names.

Everything that could change when the CoinMarketCap website changes lives here,
so the rest of the code never needs to be edited.
"""

from pathlib import Path

# --- Project paths ------------------------------------------------------------

BASE_DIR = Path(__file__).resolve().parent.parent
DATA_DIR = BASE_DIR / "data"
SNAPSHOT_DIR = DATA_DIR / "snapshots"
HISTORY_DIR = DATA_DIR / "history"
LOG_DIR = BASE_DIR / "logs"
LOG_FILE = LOG_DIR / "tracker.log"

HISTORY_FILENAME = "crypto_history.csv"

# --- Target website -----------------------------------------------------------

TARGET_URL = "https://coinmarketcap.com/"

# Host part of TARGET_URL, used by the DNS fallback.
TARGET_HOST = TARGET_URL.split("//", 1)[-1].split("/", 1)[0]

# --- DNS fallback ------------------------------------------------------------

# Some networks answer DNS queries for the target host with an unrelated
# address that accepts no connections. When the system resolver's answer
# cannot be reached, the host is re-resolved over DNS-over-HTTPS and Chrome is
# pointed at a verified address with --host-resolver-rules. Where the system
# DNS works (the normal case) nothing is overridden and Chrome resolves the
# host itself.
DNS_FALLBACK_ENABLED = True
DNS_RESOLVE_TIMEOUT = 10

# How many times the DoH candidates are verified before giving up, and how long
# to wait between attempts. CDN edges intermittently reset the connection
# during the TLS handshake, so a single failed attempt is not conclusive.
DNS_VERIFY_ATTEMPTS = 3
DNS_RETRY_DELAY = 1

# DNS-over-HTTPS resolvers, tried in order. Each is an HTTPS JSON endpoint.
DOH_RESOLVERS = (
    "https://cloudflare-dns.com/dns-query",
    "https://dns.google/resolve",
)

# Number of coins collected and exported. The website renders rows lazily and
# its leading rows can be non-coin entries, so scraper.py deliberately reads a
# few extra rows (see SCRAPE_ROW_SLACK) and the cut is made after filtering.
COIN_COUNT = 10

# Extra table rows read beyond COIN_COUNT, so index products, ad slots and
# other non-coin rows cannot consume Top 10 slots.
SCRAPE_ROW_SLACK = 5

# Seconds to wait for the table to appear and for elements to be interactable.
PAGE_LOAD_TIMEOUT = 60
ELEMENT_TIMEOUT = 20

# Seconds to wait after clicking a cookie / consent banner.
MODAL_DISMISS_TIMEOUT = 5

# --- Selenium -----------------------------------------------------------------

CHROME_WINDOW_SIZE = "1920,1080"

USER_AGENT = (
    "Mozilla/5.0 (Windows NT 10.0; Win64; x64) AppleWebKit/537.36 "
    "(KHTML, like Gecko) Chrome/154.0.0.0 Safari/537.36"
)

# --- CSS selectors ------------------------------------------------------------

TABLE_CANDIDATES = (
    "table",
    "[class*='table'][class*='cryptocurrency']",
    "div[data-testid='cryptocurrency-table'] table",
)

TABLE_ROW_SELECTOR = "tbody tr"
TABLE_HEADER_SELECTOR = "thead th"

# Header keyword -> field. Matching is case-insensitive and uses "contains",
# so "Market Cap", "Market Cap (USD)" and "MarketCap" all resolve.
HEADER_KEYWORDS = {
    "rank": ("#", "rank"),
    "name": ("name",),
    "price": ("price",),
    "change_24h": ("24h %", "24h%", "24 h", "change", "%"),
    "market_cap": ("market cap", "mcap"),
}

# Used when a row cell does not expose the dedicated class names.
COIN_NAME_SELECTORS = (
    "p.coinmarketcap-name",
    "[class*='coinmarketcap-name']",
    "[data-testid='coin-name']",
    "span[class*='name']",
)

COIN_SYMBOL_SELECTORS = (
    "p.coinmarketcap-symbol",
    "[class*='coinmarketcap-symbol']",
    "[data-testid='coin-symbol']",
    "span[class*='symbol']",
)

# Dismissed if present; failures are ignored.
COOKIE_BANNER_SELECTORS = (
    "button#onetrust-accept-btn-handler",
    "button[aria-label='Accept all']",
    "button[data-testid='accept-terms']",
    "button:contains('Accept all')",
)

# --- Output columns -----------------------------------------------------------

# Order used in every CSV file. Keep metadata columns last.
DATA_COLUMNS = ["rank", "name", "symbol", "price", "change_24h", "market_cap"]
METADATA_COLUMNS = ["run_id", "scraped_at"]
CSV_COLUMNS = DATA_COLUMNS + METADATA_COLUMNS

# pandas dtype per column, so CSVs round-trip predictably.
DTYPES = {
    "rank": "int64",
    "name": "object",
    "symbol": "object",
    "price": "float64",
    "change_24h": "float64",
    "market_cap": "float64",
    "run_id": "object",
    "scraped_at": "object",
}

# A run producing fewer rows than this is treated as a failed scrape and is
# never written to disk.
MIN_VALID_ROWS = 1

# --- Trend analysis report ----------------------------------------------------

# Columns of the trend report printed by analyze_trends.py. This is a display
# report only: it is never written to disk, so the scraper's CSV_COLUMNS and
# the stored files are unaffected.
TREND_COLUMNS = [
    "symbol",
    "name",
    "first_price",
    "last_price",
    "change_abs",
    "change_pct",
    "direction",
    "samples",
]

# A price move smaller than this many percent is reported as "flat".
FLAT_THRESHOLD_PCT = 0.01

# --- Dashboard ----------------------------------------------------------------

# Generated dashboard files. These are outputs, not scraper data, so they live in
# their own folder and never touch the snapshot or history CSVs.
DASHBOARD_DIR = DATA_DIR / "dashboard"
DASHBOARD_FILENAME = "dashboard.html"
DASHBOARD_DATA_FILENAME = "dashboard_data.json"

# Local portfolio and watchlist files. Only what the user types in is stored
# here; current prices are always derived from the latest snapshot at render
# time so they can never go stale.
PORTFOLIO_FILENAME = "portfolio.csv"
WATCHLIST_FILENAME = "watchlist.csv"

PORTFOLIO_COLUMNS = ["symbol", "name", "quantity", "avg_buy_price", "added_at"]
WATCHLIST_COLUMNS = ["symbol", "name", "note", "added_at"]

# The dashboard header warns when the newest snapshot is older than this.
STALE_AFTER_HOURS = 6