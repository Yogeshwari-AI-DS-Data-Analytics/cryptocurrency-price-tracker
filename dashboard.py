"""Entry point: python dashboard.py"""

import argparse
import sys
import time
import webbrowser
from typing import List, Optional

from crypto_tracker import config, dashboard
from crypto_tracker.cli import setup_logging


def build_parser() -> argparse.ArgumentParser:
    """Command-line options for the dashboard."""
    parser = argparse.ArgumentParser(
        prog="dashboard",
        description=(
            "Build a static HTML dashboard from the scraper's snapshot and "
            "history CSV files, and open it in your browser."
        ),
    )
    parser.add_argument(
        "--min-price", type=float, default=None,
        help="only show coins priced at or above this value",
    )
    parser.add_argument(
        "--max-price", type=float, default=None,
        help="only show coins priced at or below this value",
    )
    parser.add_argument(
        "--min-change", type=float, default=None,
        help="only show coins with 24h change at or above this percentage",
    )
    parser.add_argument(
        "--max-change", type=float, default=None,
        help="only show coins with 24h change at or below this percentage",
    )
    parser.add_argument(
        "--no-open", action="store_true",
        help="write the files but do not launch a browser",
    )
    parser.add_argument(
        "--watch", action="store_true",
        help="rebuild the dashboard every --interval seconds until Ctrl+C",
    )
    parser.add_argument(
        "--interval", type=int, default=60,
        help="seconds between rebuilds in --watch mode (default: 60)",
    )
    parser.add_argument(
        "--add-holding", metavar="SYMBOL:QUANTITY:AVG_PRICE", default=None,
        help="save a holding, e.g. BTC:0.5:42000",
    )
    parser.add_argument(
        "--remove-holding", metavar="SYMBOL", default=None,
        help="delete a saved holding",
    )
    parser.add_argument(
        "--add-watch", metavar="SYMBOL", default=None,
        help="add a symbol to the watchlist",
    )
    parser.add_argument(
        "--remove-watch", metavar="SYMBOL", default=None,
        help="remove a symbol from the watchlist",
    )
    return parser


def apply_edits(args: argparse.Namespace) -> str:
    """Apply any portfolio/watchlist edits.

    Returns "changed" if something was stored, "clean" if there was nothing to
    do, or "invalid" if the user typed something unusable (in which case a
    message has already been printed).
    """
    changed = False

    if args.add_holding:
        parts = args.add_holding.split(":")
        if len(parts) != 3:
            print(
                "--add-holding needs SYMBOL:QUANTITY:AVG_PRICE, "
                f"for example BTC:0.5:42000 (got '{args.add_holding}')"
            )
            return "invalid"
        symbol, quantity, avg_price = parts
        try:
            dashboard.add_holding(symbol, float(quantity), float(avg_price))
        except ValueError:
            print(
                "--add-holding quantity and average price must be numbers, "
                f"for example BTC:0.5:42000 (got '{args.add_holding}')"
            )
            return "invalid"
        changed = True

    if args.remove_holding:
        dashboard.remove_holding(args.remove_holding)
        changed = True

    if args.add_watch:
        dashboard.add_watch(args.add_watch)
        changed = True

    if args.remove_watch:
        dashboard.remove_watch(args.remove_watch)
        changed = True

    return "changed" if changed else "clean"


def build_once(args: argparse.Namespace) -> int:
    """Build the dashboard one time. Returns a process exit code."""
    snapshot = dashboard.load_latest_snapshot()

    if snapshot.empty:
        print("No snapshot data found, so there is nothing to display yet.")
        print(f"Run 'python main.py' first to scrape the top "
              f"{config.COIN_COUNT} coins, then run this again.")
        return 1

    # A missing or broken history file only blanks the trend columns.
    history = dashboard.load_history()

    data = dashboard.build_dashboard_data(
        snapshot,
        history,
        min_price=args.min_price,
        max_price=args.max_price,
        min_change=args.min_change,
        max_change=args.max_change,
    )

    html_path, json_path = dashboard.write_dashboard(data)

    print()
    print(f"Coins shown:      {data['shown_coins']} of {data['total_coins']}")
    print(f"Latest update:    {data['latest_scraped_at'] or 'never'}")
    print(f"Runs on record:   {data['total_runs']}")
    print(f"Holdings:         {len(data['portfolio'])}")
    print(f"Watchlist entries:{len(data['watchlist']):>3}")
    print()
    print(f"Dashboard: {html_path}")
    print(f"Data:      {json_path}")

    if not args.no_open:
        webbrowser.open(html_path.as_uri())

    return 0


def run(args: argparse.Namespace) -> int:
    """Apply edits, then build the dashboard once or repeatedly."""
    status = apply_edits(args)

    if status == "invalid":
        return 2

    if status == "changed":
        # Edits are already saved to their CSV files, so there is no need to
        # also render a page for a command that was only asked to store data.
        print("Saved. Run 'python dashboard.py' to see the updated page.")
        return 0

    if not args.watch:
        return build_once(args)

    while True:
        code = build_once(args)
        if code != 0:
            return code
        print(f"Rebuilding in {args.interval}s. Press Ctrl+C to stop.")
        time.sleep(args.interval)


def main(argv: Optional[List[str]] = None) -> int:
    """Parse arguments and run, converting expected failures into exit codes."""
    args = build_parser().parse_args(argv)
    setup_logging()

    try:
        return run(args)
    except dashboard.DashboardError as exc:
        print(f"Dashboard failed: {exc}")
        return 1
    except KeyboardInterrupt:
        print("\nStopped.")
        return 130


if __name__ == "__main__":
    sys.exit(main())
