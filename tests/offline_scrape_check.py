"""Build a CoinMarketCap-shaped HTML page and run the real scraper against it.

Lets the Selenium extraction path be verified without hitting the live site.
"""
import sys
import tempfile
from pathlib import Path

sys.path.insert(0, str(Path(__file__).resolve().parents[1]))

from crypto_tracker import config, dataframe, scraper, storage  # noqa: E402

COINS = [
    (1, "Bitcoin", "BTC", "$64,123.45", "+2.45%", "$1.23T"),
    (2, "Ethereum", "ETH", "$3,000.00", "-1.20%", "$360.0B"),
    (3, "Tether", "USDT", "$1.00", "+0.01%", "$140.0B"),
    (4, "BNB", "BNB", "$580.10", "+0.80%", "$85.5B"),
    (5, "Solana", "SOL", "$150.25", "+8.00%", "$70.0B"),
    (6, "USDC", "USDC", "$1.00", "-0.02%", "$32.0B"),
    (7, "XRP", "XRP", "$0.5234", "-2.30%", "$29.0B"),
    (8, "Dogecoin", "DOGE", "$0.1580", "+5.60%", "$23.0B"),
    (9, "Toncoin", "TON", "$5.42", "+1.10%", "$16.0B"),
    (10, "Cardano", "ADA", "$0.4523", "-3.40%", "$16.0B"),
]

ROW_TEMPLATE = """
<tr>
  <td>{rank}</td>
  <td><div class="coin-cell">
        <span class="img-placeholder"></span>
        <div class="coin-names">
          <p class="coinmarketcap-name">{name}</p>
          <p class="coinmarketcap-symbol">{symbol}</p>
        </div>
      </div></td>
  <td><p>{price}</p></td>
  <td><div class="change"><span>{change}</span></div></td>
  <td><p>{market_cap}</p></td>
  <td><p>N/A</p></td>
</tr>
"""


def build_html(rows):
    body = "".join(
        ROW_TEMPLATE.format(rank=r, name=n, symbol=s, price=p, change=c, market_cap=m)
        for r, n, s, p, c, m in rows
    )
    return f"""<!doctype html>
<html><head><meta charset="utf-8"><title>Mock CMC</title></head>
<body>
<table class="table">
  <thead><tr>
    <th class="left">#</th><th class="left">Name</th><th class="right">Price</th>
    <th class="right">24h %</th><th class="right">Market Cap</th><th class="right">FDV</th>
  </tr></thead>
  <tbody>{body}</tbody>
</table>
</body></html>"""


def scrape_file(url):
    """Reuse scraper.scrape_top_coins but point it at a local file."""
    original_url = config.TARGET_URL
    config.TARGET_URL = url
    try:
        return scraper.scrape_top_coins(headless=True)
    finally:
        config.TARGET_URL = original_url


def main():
    # Include non-coin rows the real page contains.
    rows = COINS + [
        (None, "Sponsored", "", "$1,000", "+5.00%", "$1.0B"),
        (None, "Explore assets", "", "", "", ""),
    ]
    with tempfile.TemporaryDirectory() as tmp:
        page = Path(tmp) / "mock.html"
        page.write_text(build_html(rows), encoding="utf-8")
        url = page.as_uri()

        print("== scrape_top_coins (real Selenium, local page) ==")
        raw = scrape_file(url)
        print(f"raw rows returned: {len(raw)}")

        frame = dataframe.build_dataframe(raw)
        print("\n== build_dataframe ==")
        print(frame.to_string(index=False))
        print(f"\ncolumns: {list(frame.columns)}")
        print(f"dtypes: {dict(frame.dtypes.astype(str))}")

        run_id = dataframe.make_run_id()
        stamped = dataframe.add_metadata(frame, run_id=run_id)
        print(f"\n== add_metadata (run_id={run_id}) ==")
        print(stamped.head(3).to_string(index=False))

        print("\n== filter min_price=100 ==")
        print(dataframe.apply_filters(frame, min_price=100).to_string(index=False))
        print("\n== filter min_change=0 ==")
        print(dataframe.apply_filters(frame, min_change=0).to_string(index=False))

        with tempfile.TemporaryDirectory() as out:
            config.SNAPSHOT_DIR = Path(out) / "snapshots"
            config.HISTORY_DIR = Path(out) / "history"
            storage.ensure_directories()
            snap = storage.save_snapshot(stamped, run_id)
            hist = storage.append_history(stamped, run_id)
            print(f"\n== storage ==")
            print(f"snapshot: {snap.name} ({len(stamped)} rows)")
            print(f"history:  {hist.name}")
            print("history csv:")
            print(hist.read_text(encoding="utf-8").splitlines()[0])
            print(f"...{len(hist.read_text(encoding='utf-8').splitlines()) - 1} data lines")


if __name__ == "__main__":
    main()