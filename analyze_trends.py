"""Entry point: python analyze_trends.py"""

import sys

from crypto_tracker.cli import setup_logging
from crypto_tracker.trends import (
    TrendError,
    build_price_trend,
    format_trend,
    load_history,
    summarize,
)


def main() -> int:
    """Print a price trend report built from the history CSV."""
    setup_logging()

    try:
        history = load_history()
        trend = build_price_trend(history)
    except TrendError as exc:
        print(f"Trend analysis failed: {exc}")
        return 1

    if trend.empty:
        print("No history to analyse yet. Run main.py first.")
        return 0

    counts = summarize(trend)
    print()
    print(format_trend(trend))
    print()
    print(
        "Up: {}   Down: {}   Flat: {}   No data: {}".format(
            counts["up"], counts["down"], counts["flat"], counts["unknown"]
        )
    )
    print(
        "\nA coin needs to appear in at least 2 runs before a trend can be "
        "calculated."
    )
    return 0


if __name__ == "__main__":
    sys.exit(main())
