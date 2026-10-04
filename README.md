# Cryptocurrency Price Tracker

A command-line Python app that scrapes the top cryptocurrencies from
[CoinMarketCap](https://coinmarketcap.com/) with Selenium, cleans the data with
pandas, and exports it to timestamped CSV files.

The site renders its table with JavaScript, so `requests` + BeautifulSoup only
ever sees an empty table. Selenium runs a real Chrome browser and reads what is
actually on screen.

## Features

- Always scrapes exactly the top 10 coins: rank, name, symbol, price, 24h % change, market cap
- Handles messy formats: `$64,123.45`, `$1.23T`, `+2.45%`, `N/A`, em/en dashes
- Exports CSV: one snapshot per run, plus an appended history file
- Timestamps every run (`run_id` + ISO 8601 `scraped_at`)
- Headless Chrome by default, with `--no-headless` to watch it work
- Optional filtering by price and 24h change
- Explicit error handling with meaningful exit codes; never writes an empty CSV
- 92 unit tests, none of which need a browser or network

## Requirements

- Python 3.9+
- Google Chrome installed (webdriver_manager downloads the matching chromedriver)

`webdriver_manager` is the primary ChromeDriver provider: every run asks it for
the chromedriver binary that matches the installed Chrome and launches Selenium
with that path.

There is exactly one exception. On Windows, Application Control (WDAC) sometimes
refuses to execute a freshly downloaded driver, which surfaces as
`OSError: [WinError 4551] An Application Control policy has blocked this file`.
Only for that specific error does `scraper.py` retry once using Selenium's own
driver resolution, so the tracker keeps working on locked-down machines. No
other error falls back — a genuine misconfiguration is still reported as an
error with a non-zero exit code, and no Windows security setting is ever
modified.

## Installation

```bash
# 1. Create the virtual environment
python -m venv venv

# 2. Activate it
#    Windows
venv\Scripts\activate
#    macOS / Linux
source venv/bin/activate

# 3. Install dependencies
pip install -r requirements.txt
```

## Usage

The tracker always collects exactly the top 10 cryptocurrencies. There is no
option to change that.

```bash
# Top 10, headless (defaults)
python main.py

# Watch the browser work
python main.py --no-headless

# Only coins priced at or above $100
python main.py --min-price 100

# Price range
python main.py --min-price 100 --max-price 50000

# Only coins that gained at least 2% today
python main.py --min-change 2

# Combined filters are ANDed
python main.py --min-price 1000 --max-change 5

# Skip appending to the history file
python main.py --no-history

# Debug logging
python main.py -v
```

`python main.py --help` lists every option.

### Output

```
 Rank     Name Symbol      Price    24h %         Market Cap
    1  Bitcoin    BTC $64,123.45 +2.45% $1,230,000,000,000
    2 Ethereum    ETH  $3,000.00 -1.20%   $360,000,000,000
```

### CLI options

| Option | Default | Description |
| --- | --- | --- |
| `--headless` / `--no-headless` | headless | Run Chrome without a visible window |
| `--min-price` | none | Keep coins priced at or above this value |
| `--max-price` | none | Keep coins priced at or below this value |
| `--min-change` | none | Keep coins with 24h change at or above this percentage |
| `--max-change` | none | Keep coins with 24h change at or below this percentage |
| `--no-history` | off | Write the snapshot only, skip the history file |
| `-v`, `--verbose` | off | Enable debug logging |

### Output files

```
data/
├── snapshots/
│   └── crypto_20261002_141530.csv   one file per run, wide format
└── history/
    └── crypto_history.csv           every run appended, for trend analysis
logs/
└── tracker.log                       rotating log, 1 MB x 3
```

Both CSVs share these columns:

| Column | Type | Notes |
| --- | --- | --- |
| `rank` | int | Position in the table |
| `name` | text | e.g. `Bitcoin` |
| `symbol` | text | e.g. `BTC` |
| `price` | float | USD, no currency symbol or separators |
| `change_24h` | float | Percent, signed |
| `market_cap` | float | Full USD value, e.g. `1.23T` becomes `1.23e12` |
| `run_id` | text | `YYYYMMDD_HHMMSS`, shared by one run's files |
| `scraped_at` | text | ISO 8601 local timestamp, e.g. `2026-10-02T14:15:30` |

Rows with the same `run_id` are replaced rather than duplicated in
`crypto_history.csv`, so re-running a `run_id` is safe.

## Project structure

```
.
├── main.py                     entry point
├── requirements.txt
├── conftest.py                 puts the project root on sys.path for pytest
├── crypto_tracker/
│   ├── config.py               URL, selectors, timeouts, paths, column names
│   ├── scraper.py              Chrome setup, driver, waits, scrolling, extraction
│   ├── parser.py               text -> number conversions
│   ├── dataframe.py            build Top 10, filter, timestamp
│   ├── storage.py              write snapshot + history CSVs
│   ├── dns_resolver.py         verify the target host, DoH fallback
│   └── cli.py                  arguments, logging, orchestration
├── data/                       generated CSV files
├── logs/                       generated log files
└── tests/
```

Each module has one job. `parser.py` imports neither Selenium nor pandas, so the
data logic is testable in isolation; `scraper.py` never touches the filesystem;
`storage.py` never opens a browser.

### Data flow

```
scrape_top_coins()      Selenium -> list[dict] of raw text
      |                (COIN_COUNT + SCRAPE_ROW_SLACK rows, deliberately
      |                 over-collected so non-coin rows cannot consume slots)
      v
build_dataframe()       parser.py -> typed, sorted, de-duplicated DataFrame,
      |                non-coin rows dropped, then cut to the Top 10
      v
apply_filters()         pandas boolean masks (ANDed)
      v
add_metadata()          run_id + scraped_at
      v
save_snapshot()         data/snapshots/crypto_<run_id>.csv
append_history()        data/history/crypto_history.csv
```

## Tests

```bash
pytest -q
```

Covers the number parsing, filtering, metadata, CSV writing (including
corrupt-history recovery and run-ID collisions), driver creation (including the
narrow Windows Application Control fallback), and the full CLI with a mocked
scraper.

To exercise the real Selenium extraction path without hitting the live site:

```bash
python tests/offline_scrape_check.py
```

This builds a CoinMarketCap-shaped page locally, runs the actual scraper against
it, and prints the parsed DataFrame, filters and CSV output.

## Troubleshooting

**`net::ERR_CONNECTION_TIMED_OUT`** — the site is unreachable from your network.
Test it directly:

```bash
Test-NetConnection coinmarketcap.com -Port 443
```

Some networks answer DNS queries with a dead address rather than refusing the
connection. `dns_resolver.py` detects this automatically: it probes the system
answer, falls back to DNS-over-HTTPS, and pins a TLS-verified address for the run
via `--host-resolver-rules`. Nothing outside the app is modified, and the startup
log records whether a pin was applied. Set `DNS_FALLBACK_ENABLED = False` in
`config.py` to turn this off.

If the address itself is blocked — a firewall, VPN or ISP that drops the
connection even after a correct lookup — there is no code fix for that; it needs
an unrestricted connection.

**`No cryptocurrency table found`** — the page loaded but the table did not.
Either the markup changed (update selectors in `config.py`, `HEADER_KEYWORDS`
and the `*_SELECTORS` tuples) or a bot-protection challenge was served. Try
`python main.py --no-headless` to see what Chrome is displaying.

**`Could not obtain chromedriver via webdriver_manager`** — webdriver_manager
could not look up or download a driver for the installed Chrome. Check the
network connection and verify Chrome is installed, then try
`pip install --upgrade webdriver-manager`.

**`[WinError 4551] An Application Control policy has blocked this file`** —
Windows blocked the chromedriver that webdriver_manager downloaded. The app
logs a warning and automatically retries with Selenium's own driver resolution,
so the run normally still succeeds. To remove the warning permanently, ask your
administrator to allow the driver path shown in the log
(`%USERPROFILE%\.wdm\...`).

**`Could not start Chrome`** — Chrome is not installed, or the downloaded
chromedriver does not match its version. Verify Chrome is installed and try
`pip install --upgrade selenium`.

**`Could not identify the price column`** — header labels changed. Update
`HEADER_KEYWORDS["price"]` in `config.py`.

**ImportError: DLL load failed while importing pandas** — Windows Application
Control is blocking the pandas 3.x binary. `requirements.txt` pins
`pandas<3.0` to avoid this.

## Notes on scraping CoinMarketCap

- **Lazy loading** — rows render only as the page scrolls, so the scraper
  scrolls in steps until enough rows are attached to the DOM, and gives up
  early if the row count stops growing. Without this you would capture
  duplicate rows.
- **Selector rot** — CoinMarketCap changes class names often. Every selector
  lives in `config.py` so a fix is a one-line edit. Columns are matched by
  header text, not position, so inserting a column does not shift the data.
- **Bot protection** — a realistic user agent and `--lang=en-US` are set, and
  overlay banners are dismissed before reading. If a challenge page is served,
  the app exits with an error instead of writing a bad file.
- **Non-coin rows** — ad slots, "Explore assets" banners and index products
  (such as the CoinMarketCap 20 Index DTF) appear inside the table, sometimes
  as the very first row. They are filtered out because `rank` will not parse as
  an integer, and the scraper reads extra rows (`SCRAPE_ROW_SLACK`) so those
  entries cannot push a real coin out of the Top 10.
- **Be polite** — one page load per run, no retries in a tight loop.

## License

For educational use. Respect CoinMarketCap's terms of service and `robots.txt`
if you run this repeatedly.