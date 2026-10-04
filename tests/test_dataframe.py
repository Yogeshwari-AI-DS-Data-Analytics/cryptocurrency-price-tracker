"""Unit tests for DataFrame building, filtering and metadata."""

import pandas as pd
import pytest

from crypto_tracker import config, dataframe

RAW_ROWS = [
    {"rank": "1", "name": "Bitcoin", "symbol": "BTC", "price": "$64,123.45", "change_24h": "+2.45%", "market_cap": "$1.23T"},
    {"rank": "2", "name": "Ethereum", "symbol": "ETH", "price": "$3,000.00", "change_24h": "-1.20%", "market_cap": "$360.0B"},
    {"rank": "3", "name": "Solana", "symbol": "SOL", "price": "$150.25", "change_24h": "+8.00%", "market_cap": "$70.0B"},
]


def test_build_dataframe_types_and_order():
    frame = dataframe.build_dataframe(RAW_ROWS)

    assert list(frame.columns) == config.DATA_COLUMNS
    assert len(frame) == 3
    assert frame["rank"].tolist() == [1, 2, 3]
    assert frame["price"].dtype == "float64"
    assert frame["price"].iloc[0] == pytest.approx(64123.45)
    assert frame["market_cap"].iloc[0] == pytest.approx(1.23e12)
    assert frame["change_24h"].iloc[1] == pytest.approx(-1.20)


INDEX_ROW = {
    "rank": "", "name": "CoinMarketCap 20 Index DTF", "symbol": "CMC20",
    "price": "$174.93", "change_24h": "0.13%", "market_cap": "$6.27M",
}


def make_coins(count):
    return [
        {
            "rank": str(i), "name": f"Coin{i}", "symbol": f"C{i}",
            "price": f"${i}.00", "change_24h": "+1.00%", "market_cap": "$1.0B",
        }
        for i in range(1, count + 1)
    ]


def test_build_dataframe_caps_at_coin_count():
    """Always Top 10, never more."""
    frame = dataframe.build_dataframe(make_coins(14))
    assert len(frame) == config.COIN_COUNT
    assert frame["rank"].tolist() == list(range(1, config.COIN_COUNT + 1))
    assert frame["name"].iloc[-1] == "Coin10"


def test_build_dataframe_keeps_tenth_coin_despite_leading_index_row():
    """A leading index product must not cost the 10th coin its place."""
    rows = [INDEX_ROW] + make_coins(10)

    frame = dataframe.build_dataframe(rows)

    assert len(frame) == config.COIN_COUNT
    assert frame["rank"].tolist() == list(range(1, config.COIN_COUNT + 1))
    assert frame["name"].iloc[-1] == "Coin10"
    assert "CMC20" not in frame["symbol"].tolist()


def test_build_dataframe_never_invents_rows_when_fewer_coins_exist():
    rows = [INDEX_ROW] + RAW_ROWS[:4]
    frame = dataframe.build_dataframe(rows)
    assert len(frame) == 3


def test_build_dataframe_keeps_everything_valid_when_under_cap():
    rows = [INDEX_ROW] + RAW_ROWS
    frame = dataframe.build_dataframe(rows)
    assert len(frame) == len(RAW_ROWS)


def test_build_dataframe_drops_non_coin_rows():
    rows = RAW_ROWS + [
        {"rank": "Sponsored", "name": "Sponsored", "symbol": "", "price": "—", "change_24h": "", "market_cap": ""},
        {"rank": "Explore assets", "name": "Explore assets", "symbol": "", "price": "", "change_24h": "", "market_cap": ""},
    ]
    frame = dataframe.build_dataframe(rows)
    assert len(frame) == 3


def test_build_dataframe_handles_missing_values():
    rows = [{"rank": "1", "name": "Bitcoin", "symbol": "BTC", "price": "N/A", "change_24h": "", "market_cap": "N/A"}]
    frame = dataframe.build_dataframe(rows)
    assert len(frame) == 1
    assert pd.isna(frame["price"].iloc[0])


def test_build_dataframe_empty_input():
    frame = dataframe.build_dataframe([])
    assert frame.empty
    assert list(frame.columns) == config.DATA_COLUMNS


def test_filters_by_price():
    frame = dataframe.build_dataframe(RAW_ROWS)
    result = dataframe.apply_filters(frame, min_price=200)
    assert result["symbol"].tolist() == ["BTC", "ETH"]


def test_filters_by_price_range():
    frame = dataframe.build_dataframe(RAW_ROWS)
    result = dataframe.apply_filters(frame, min_price=100, max_price=1000)
    assert result["symbol"].tolist() == ["SOL"]


def test_filters_by_change():
    frame = dataframe.build_dataframe(RAW_ROWS)
    result = dataframe.apply_filters(frame, min_change=0)
    assert result["symbol"].tolist() == ["BTC", "SOL"]


def test_filters_combined_are_anded():
    frame = dataframe.build_dataframe(RAW_ROWS)
    result = dataframe.apply_filters(frame, min_price=200, max_change=0)
    assert result["symbol"].tolist() == ["ETH"]


def test_filters_exclude_rows_with_missing_values():
    rows = RAW_ROWS + [{"rank": "4", "name": "Unknown", "symbol": "UNK", "price": "N/A", "change_24h": "+5.00%", "market_cap": "$1.0B"}]
    frame = dataframe.build_dataframe(rows)
    result = dataframe.apply_filters(frame, min_price=1)
    assert "UNK" not in result["symbol"].tolist()


def test_add_metadata_adds_columns_in_order():
    frame = dataframe.build_dataframe(RAW_ROWS)
    stamped = dataframe.add_metadata(frame, run_id="20261002_141530")

    assert list(stamped.columns) == config.CSV_COLUMNS
    assert stamped["run_id"].unique().tolist() == ["20261002_141530"]
    assert stamped["scraped_at"].iloc[0].startswith("20")


def test_add_metadata_does_not_mutate_input():
    frame = dataframe.build_dataframe(RAW_ROWS)
    dataframe.add_metadata(frame, run_id="x")
    assert "run_id" not in frame.columns


def test_apply_filters_on_empty_frame():
    assert dataframe.apply_filters(pd.DataFrame(columns=config.DATA_COLUMNS), min_price=1).empty


def test_make_run_id_format():
    from datetime import datetime

    assert dataframe.make_run_id(datetime(2026, 10, 2, 14, 15, 30)) == "20261002_141530"