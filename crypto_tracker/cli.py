"""Command-line entry point: argument parsing, logging, and orchestration."""

import argparse
import logging
import sys
from logging.handlers import RotatingFileHandler
from typing import Optional

import pandas as pd

from . import config, dataframe, storage
from .scraper import ScraperError, scrape_top_coins

LOG_FORMAT = "%(asctime)s %(levelname)-8s %(name)s: %(message)s"


def setup_logging(verbose: bool = False) -> None:
    """Log to stdout and to logs/tracker.log (rotating at 1 MB)."""
    root = logging.getLogger()
    root.setLevel(logging.DEBUG if verbose else logging.INFO)
    root.handlers.clear()

    formatter = logging.Formatter(LOG_FORMAT)

    console = logging.StreamHandler(sys.stdout)
    console.setLevel(logging.DEBUG if verbose else logging.INFO)
    console.setFormatter(formatter)
    root.addHandler(console)

    try:
        storage.ensure_directories()
        file_handler = RotatingFileHandler(
            config.LOG_FILE, maxBytes=1_000_000, backupCount=3, encoding="utf-8"
        )
        file_handler.setLevel(logging.DEBUG)
        file_handler.setFormatter(formatter)
        root.addHandler(file_handler)
    except OSError as exc:
        root.warning("File logging disabled (%s)", exc)

    # The Selenium wire protocol is extremely noisy at DEBUG level.
    logging.getLogger("selenium").setLevel(logging.WARNING)
    logging.getLogger("urllib3").setLevel(logging.WARNING)
    logging.getLogger("websockets").setLevel(logging.WARNING)


def build_parser() -> argparse.ArgumentParser:
    parser = argparse.ArgumentParser(
        prog="crypto-tracker",
        description=(
            f"Scrape the top {config.COIN_COUNT} cryptocurrencies from "
            "CoinMarketCap and export them to CSV files in data/."
        ),
    )
    parser.add_argument(
        "--headless",
        action=argparse.BooleanOptionalAction,
        default=True,
        help="run Chrome without a visible window (default: enabled; "
        "use --no-headless to watch it work)",
    )
    parser.add_argument(
        "--min-price", type=float, default=None, help="keep coins priced at or above this value"
    )
    parser.add_argument(
        "--max-price", type=float, default=None, help="keep coins priced at or below this value"
    )
    parser.add_argument(
        "--min-change", type=float, default=None, help="keep coins with 24h change at or above this percentage"
    )
    parser.add_argument(
        "--max-change", type=float, default=None, help="keep coins with 24h change at or below this percentage"
    )
    parser.add_argument(
        "--no-history",
        action="store_true",
        help="write the snapshot CSV only, skip appending to history",
    )
    parser.add_argument(
        "-v", "--verbose", action="store_true", help="enable debug logging"
    )
    return parser


def format_table(frame: pd.DataFrame) -> str:
    """Readable terminal summary of the collected rows."""
    if frame.empty:
        return "No rows matched the given filters."

    display = frame[config.DATA_COLUMNS].copy()
    display["price"] = display["price"].map(
        lambda value: f"${value:,.2f}" if pd.notna(value) else "-"
    )
    display["change_24h"] = display["change_24h"].map(
        lambda value: f"{value:+.2f}%" if pd.notna(value) else "-"
    )
    display["market_cap"] = display["market_cap"].map(
        lambda value: f"${value:,.0f}" if pd.notna(value) else "-"
    )
    display.columns = [
        "Rank", "Name", "Symbol", "Price", "24h %", "Market Cap"
    ]
    return display.to_string(index=False)


def run(args: argparse.Namespace) -> int:
    """Execute one scrape-and-export run. Returns a process exit code."""
    logger = logging.getLogger(__name__)

    run_id = storage.unique_run_id(dataframe.make_run_id())
    logger.info(
        "Starting run %s (top %s, headless=%s)",
        run_id,
        config.COIN_COUNT,
        args.headless,
    )

    raw_rows = scrape_top_coins(headless=args.headless)
    frame = dataframe.build_dataframe(raw_rows)

    if len(frame) < config.MIN_VALID_ROWS:
        logger.error(
            "Scraped %s valid row(s); treating this run as failed and not "
            "writing any CSV.",
            len(frame),
        )
        return 1

    logger.info("Scraped %s coin(s)", len(frame))

    filtered = dataframe.apply_filters(
        frame,
        min_price=args.min_price,
        max_price=args.max_price,
        min_change=args.min_change,
        max_change=args.max_change,
    )

    if filtered.empty:
        logger.warning(
            "All %s row(s) were removed by the filters; no CSV written.",
            len(frame),
        )
        print(format_table(filtered))
        return 0

    stamped = dataframe.add_metadata(filtered, run_id=run_id)

    snapshot_file = storage.save_snapshot(stamped, run_id)
    if not args.no_history:
        storage.append_history(stamped, run_id)

    print()
    print(format_table(stamped))
    print()
    print(f"Snapshot: {snapshot_file}")
    if not args.no_history:
        print(f"History:  {storage.history_path()}")
    print(f"Run ID:   {run_id}")

    return 0


def main(argv: Optional[list] = None) -> int:
    """Parse arguments and run, converting expected failures into exit codes."""
    args = build_parser().parse_args(argv)
    setup_logging(verbose=args.verbose)

    try:
        return run(args)
    except (ScraperError, storage.StorageError) as exc:
        logging.getLogger(__name__).error("%s", exc)
        return 1
    except KeyboardInterrupt:
        logging.getLogger(__name__).warning("Interrupted by user")
        return 130
    except Exception as exc:  # noqa: BLE001 - last-resort safety net
        logging.getLogger(__name__).critical("Unexpected failure: %s", exc, exc_info=True)
        return 1


if __name__ == "__main__":
    sys.exit(main())