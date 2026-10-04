"""Trend analysis: compares coin prices across the runs in the history CSV.

Reads ``data/history/crypto_history.csv`` (written by storage.append_history)
and reports, per coin, how its price moved between the earliest and the latest
run on record. The CSV is the data set and pandas does the work, so there is no
database and no web framework involved.

Nothing here writes a file; the history CSV is only ever read.
"""

import logging

import pandas as pd

from . import config, storage

logger = logging.getLogger(__name__)


class TrendError(Exception):
    """Raised when the history CSV cannot be analysed."""


def load_history() -> pd.DataFrame:
    """Read the history CSV into a DataFrame, oldest run first.

    Returns an empty DataFrame when no history file exists yet, so a first-ever
    scrape does not crash the analysis.
    """
    path = storage.history_path()

    if not path.exists():
        logger.warning("No history file at %s; nothing to analyse yet.", path)
        return pd.DataFrame(columns=config.CSV_COLUMNS)

    try:
        history = pd.read_csv(path)
    except (OSError, pd.errors.EmptyDataError, pd.errors.ParserError) as exc:
        raise TrendError(f"Could not read {path}: {exc}") from exc

    if history.empty:
        return history

    # dataframe.add_metadata writes scraped_at as ISO 8601 local time.
    history["scraped_at"] = pd.to_datetime(history["scraped_at"], errors="coerce")
    history = history.sort_values(["scraped_at", "rank"]).reset_index(drop=True)

    logger.info(
        "Loaded %s row(s) across %s run(s) from %s",
        len(history),
        history["run_id"].nunique(),
        path,
    )
    return history


def build_price_trend(history: pd.DataFrame) -> pd.DataFrame:
    """Per-coin price movement between the earliest and latest run.

    One output row per coin, best riser first. ``samples`` counts how many runs
    a coin appeared in, so a coin seen only once is never presented as a trend:
    its change is left empty and its direction is "unknown".
    """
    if history.empty:
        return pd.DataFrame(columns=config.TREND_COLUMNS)

    ordered = history.sort_values(["scraped_at", "rank"]).reset_index(drop=True)

    # The first and last sighting of each coin.
    oldest = ordered.drop_duplicates(subset=["symbol"], keep="first")
    newest = ordered.drop_duplicates(subset=["symbol"], keep="last")

    # How many runs each coin appeared in, looked up by symbol.
    samples = ordered.groupby("symbol")["run_id"].nunique()

    # Join on "symbol" rather than by row position: the two frames above are
    # not guaranteed to list the coins in the same order, so lining them up
    # positionally would mix one coin's prices up with another's.
    trend = oldest[["symbol", "price"]].rename(columns={"price": "first_price"})
    trend = trend.merge(
        newest[["symbol", "name", "price"]].rename(columns={"price": "last_price"}),
        on="symbol",
    )
    trend["samples"] = trend["symbol"].map(samples)

    change = trend["last_price"] - trend["first_price"]
    has_pair = trend["samples"] >= 2

    # A single observation has no earlier price to compare against.
    trend["change_abs"] = change.where(has_pair)
    trend["change_pct"] = (
        change.div(trend["first_price"]).mul(100).where(has_pair)
    )

    def label(row):
        if pd.isna(row["change_pct"]):
            return "unknown"
        if row["change_pct"] > config.FLAT_THRESHOLD_PCT:
            return "up"
        if row["change_pct"] < -config.FLAT_THRESHOLD_PCT:
            return "down"
        return "flat"

    trend["direction"] = trend.apply(label, axis=1)

    # Biggest riser first; coins with no trend sort last.
    trend = trend.sort_values(
        "change_pct", ascending=False, na_position="last"
    ).reset_index(drop=True)

    return trend[config.TREND_COLUMNS]


def summarize(trend: pd.DataFrame) -> dict:
    """Count how many coins moved each way."""
    counts = {"up": 0, "down": 0, "flat": 0, "unknown": 0}
    if trend.empty:
        return counts
    for direction, count in trend["direction"].value_counts().items():
        counts[direction] = int(count)
    return counts


def format_trend(frame: pd.DataFrame) -> str:
    """Readable terminal summary of the trend report."""
    if frame.empty:
        return "No trend data available."

    display = frame[config.TREND_COLUMNS].copy()

    def money(value):
        return f"${value:,.2f}" if pd.notna(value) else "-"

    display["first_price"] = display["first_price"].map(money)
    display["last_price"] = display["last_price"].map(money)
    display["change_abs"] = display["change_abs"].map(money)
    display["change_pct"] = display["change_pct"].map(
        lambda value: f"{value:+.2f}%" if pd.notna(value) else "-"
    )
    display["direction"] = display["direction"].map(
        lambda value: {"up": "UP", "down": "DOWN", "flat": "FLAT"}.get(
            value, "NO DATA"
        )
    )
    display.columns = [
        "Symbol", "Name", "First", "Last", "Change $", "Change %",
        "Trend", "Runs",
    ]
    return display.to_string(index=False)
