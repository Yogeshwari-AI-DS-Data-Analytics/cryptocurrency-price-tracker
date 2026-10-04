"""Pandas layer: turns raw rows into a clean, filtered, timestamped DataFrame."""

import logging
from datetime import datetime
from typing import Any, Dict, List, Optional

import pandas as pd

from . import config, parser

logger = logging.getLogger(__name__)


def build_dataframe(
    raw_rows: List[Dict[str, Optional[str]]]
) -> pd.DataFrame:
    """Parse raw text rows into a typed DataFrame of the Top 10 coins.

    Rows whose rank is not an integer (ad banners, "Explore assets" links,
    index products) are dropped, as are rows missing a coin name. The Top
    ``config.COIN_COUNT`` cut is made here, *after* those rows are discarded:
    truncating the raw rows beforehand would let a dropped entry cost a real
    coin its place in the results.
    """
    records: List[Dict[str, Any]] = []
    skipped = 0

    for raw in raw_rows:
        rank = parser.parse_rank(raw.get("rank"))
        name = parser.parse_name(raw.get("name"))

        if rank is None or name is None:
            skipped += 1
            continue

        records.append(
            {
                "rank": rank,
                "name": name,
                "symbol": parser.parse_symbol(raw.get("symbol")),
                "price": parser.parse_price(raw.get("price")),
                "change_24h": parser.parse_percent(raw.get("change_24h")),
                "market_cap": parser.parse_market_cap(raw.get("market_cap")),
            }
        )

    if skipped:
        logger.warning("Skipped %s non-coin row(s)", skipped)

    frame = pd.DataFrame(records, columns=config.DATA_COLUMNS)
    if frame.empty:
        return frame

    for column in config.DATA_COLUMNS:
        frame[column] = frame[column].astype(config.DTYPES[column], errors="ignore")

    # Keep the highest-ranked coin per symbol if the page repeated a row.
    frame = frame.drop_duplicates(subset=["symbol"], keep="first")
    frame = frame.sort_values("rank").reset_index(drop=True)

    if len(frame) > config.COIN_COUNT:
        logger.info(
            "Keeping the top %s coin(s) of %s", config.COIN_COUNT, len(frame)
        )
        frame = frame.head(config.COIN_COUNT)

    logger.info("Built DataFrame with %s row(s)", len(frame))
    return frame


def apply_filters(
    frame: pd.DataFrame,
    min_price: Optional[float] = None,
    max_price: Optional[float] = None,
    min_change: Optional[float] = None,
    max_change: Optional[float] = None,
) -> pd.DataFrame:
    """Filter rows by price and 24h change. All supplied bounds are ANDed.

    Rows with a missing value for the field being filtered are excluded, since
    they cannot be confirmed to satisfy the bound.
    """
    if frame.empty:
        return frame

    mask = pd.Series(True, index=frame.index)

    if min_price is not None:
        mask &= frame["price"].notna() & (frame["price"] >= min_price)
    if max_price is not None:
        mask &= frame["price"].notna() & (frame["price"] <= max_price)
    if min_change is not None:
        mask &= frame["change_24h"].notna() & (frame["change_24h"] >= min_change)
    if max_change is not None:
        mask &= frame["change_24h"].notna() & (frame["change_24h"] <= max_change)

    filtered = frame[mask].reset_index(drop=True)
    logger.info(
        "Filtering kept %s of %s row(s)", len(filtered), len(frame)
    )
    return filtered


def add_metadata(
    frame: pd.DataFrame, run_id: Optional[str] = None, scraped_at: Optional[str] = None
) -> pd.DataFrame:
    """Stamp every row with the run ID and ISO 8601 scrape time."""
    if frame.empty:
        return frame

    now = datetime.now()
    frame = frame.copy()
    frame["run_id"] = run_id or now.strftime("%Y%m%d_%H%M%S")
    frame["scraped_at"] = scraped_at or now.isoformat(timespec="seconds")

    for column in config.METADATA_COLUMNS:
        frame[column] = frame[column].astype(config.DTYPES[column])

    return frame[config.CSV_COLUMNS]


def make_run_id(moment: Optional[datetime] = None) -> str:
    """Build the ``YYYYMMDD_HHMMSS`` identifier shared by a run's files."""
    return (moment or datetime.now()).strftime("%Y%m%d_%H%M%S")