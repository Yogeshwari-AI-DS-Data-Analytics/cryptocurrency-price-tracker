"""Trend analysis tests (no browser, no network)."""

import pandas as pd
import pytest

from crypto_tracker import config, storage, trends


@pytest.fixture(autouse=True)
def temp_dirs(tmp_path, monkeypatch):
    """Point the storage paths at a throwaway directory."""
    monkeypatch.setattr(config, "DATA_DIR", tmp_path / "data")
    monkeypatch.setattr(config, "SNAPSHOT_DIR", tmp_path / "data" / "snapshots")
    monkeypatch.setattr(config, "HISTORY_DIR", tmp_path / "data" / "history")
    monkeypatch.setattr(config, "LOG_DIR", tmp_path / "logs")
    storage.ensure_directories()
    yield


def row(symbol, name, price, run_id, scraped_at, rank=1):
    """One history row, matching the columns the scraper writes."""
    return {
        "rank": rank,
        "name": name,
        "symbol": symbol,
        "price": price,
        "change_24h": 0.0,
        "market_cap": 1000.0,
        "run_id": run_id,
        "scraped_at": scraped_at,
    }


def write_history(*rows):
    """Write the given rows straight to the history CSV."""
    frame = pd.DataFrame(list(rows), columns=config.CSV_COLUMNS)
    frame.to_csv(storage.history_path(), index=False)
    return frame


# --- load_history -----------------------------------------------------------


def test_load_history_returns_empty_when_no_file():
    """A first-ever scrape leaves no history file; that must not crash."""
    history = trends.load_history()
    assert history.empty
    assert list(history.columns) == config.CSV_COLUMNS


def test_load_history_sorts_runs_oldest_first():
    write_history(
        row("BTC", "Bitcoin", 110.0, "run_2", "2026-10-03T10:00:00"),
        row("BTC", "Bitcoin", 100.0, "run_1", "2026-10-03T09:00:00"),
    )

    history = trends.load_history()

    assert list(history["price"]) == [100.0, 110.0]


def test_load_history_raises_trend_error_on_unreadable_file():
    # An empty file parses as no columns at all, which pandas reports as
    # EmptyDataError rather than a valid frame.
    storage.history_path().write_text("", encoding="utf-8")

    with pytest.raises(trends.TrendError):
        trends.load_history()


# --- build_price_trend ------------------------------------------------------


def test_build_price_trend_computes_change_between_first_and_last_run():
    write_history(
        row("BTC", "Bitcoin", 100.0, "run_1", "2026-10-03T09:00:00"),
        row("ETH", "Ethereum", 50.0, "run_1", "2026-10-03T09:00:00", rank=2),
        row("BTC", "Bitcoin", 110.0, "run_2", "2026-10-03T10:00:00"),
        row("ETH", "Ethereum", 55.0, "run_2", "2026-10-03T10:00:00", rank=2),
    )

    trend = trends.build_price_trend(trends.load_history())
    btc = trend[trend["symbol"] == "BTC"].iloc[0]

    assert btc["first_price"] == 100.0
    assert btc["last_price"] == 110.0
    assert btc["change_abs"] == pytest.approx(10.0)
    assert btc["change_pct"] == pytest.approx(10.0)
    assert btc["direction"] == "up"
    assert btc["samples"] == 2


def test_build_price_trend_uses_scraped_at_not_row_order():
    """Rows in the CSV may be in any order; scraped_at decides the sequence."""
    write_history(
        row("BTC", "Bitcoin", 110.0, "run_2", "2026-10-03T10:00:00"),
        row("BTC", "Bitcoin", 100.0, "run_1", "2026-10-03T09:00:00"),
    )

    trend = trends.build_price_trend(trends.load_history())

    assert trend.iloc[0]["first_price"] == 100.0
    assert trend.iloc[0]["last_price"] == 110.0
    assert trend.iloc[0]["change_pct"] == pytest.approx(10.0)


def test_build_price_trend_marks_single_run_coin_unknown():
    """One observation is not a trend."""
    write_history(
        row("BTC", "Bitcoin", 100.0, "run_1", "2026-10-03T09:00:00"),
        row("BTC", "Bitcoin", 110.0, "run_2", "2026-10-03T10:00:00"),
        row("SOL", "Solana", 20.0, "run_1", "2026-10-03T09:00:00", rank=2),
    )

    trend = trends.build_price_trend(trends.load_history())
    sol = trend[trend["symbol"] == "SOL"].iloc[0]

    assert sol["samples"] == 1
    assert sol["direction"] == "unknown"
    assert pd.isna(sol["change_abs"])
    assert pd.isna(sol["change_pct"])


def test_build_price_trend_labels_small_move_flat():
    """A move under the flat threshold is not reported as a real change."""
    write_history(
        row("BTC", "Bitcoin", 100.0, "run_1", "2026-10-03T09:00:00"),
        row("BTC", "Bitcoin", 100.001, "run_2", "2026-10-03T10:00:00"),
    )

    trend = trends.build_price_trend(trends.load_history())

    assert trend.iloc[0]["change_pct"] == pytest.approx(0.001)
    assert trend.iloc[0]["direction"] == "flat"


def test_build_price_trend_labels_decline_down():
    write_history(
        row("BTC", "Bitcoin", 100.0, "run_1", "2026-10-03T09:00:00"),
        row("BTC", "Bitcoin", 80.0, "run_2", "2026-10-03T10:00:00"),
    )

    trend = trends.build_price_trend(trends.load_history())

    assert trend.iloc[0]["change_abs"] == pytest.approx(-20.0)
    assert trend.iloc[0]["change_pct"] == pytest.approx(-20.0)
    assert trend.iloc[0]["direction"] == "down"


def test_build_price_trend_sorts_biggest_riser_first():
    write_history(
        row("DOWN", "Downcoin", 100.0, "run_1", "2026-10-03T09:00:00"),
        row("UP", "Upcoin", 100.0, "run_1", "2026-10-03T09:00:00", rank=2),
        row("ONE", "Onecoin", 100.0, "run_1", "2026-10-03T09:00:00", rank=3),
        row("DOWN", "Downcoin", 90.0, "run_2", "2026-10-03T10:00:00"),
        row("UP", "Upcoin", 150.0, "run_2", "2026-10-03T10:00:00", rank=2),
    )

    trend = trends.build_price_trend(trends.load_history())

    # Riser first, faller second, and the single-run coin last.
    assert trend["symbol"].tolist() == ["UP", "DOWN", "ONE"]
    assert trend.iloc[-1]["direction"] == "unknown"


def test_build_price_trend_returns_empty_frame_for_empty_history():
    trend = trends.build_price_trend(pd.DataFrame(columns=config.CSV_COLUMNS))

    assert trend.empty
    assert list(trend.columns) == config.TREND_COLUMNS


def test_build_price_trend_columns_match_config():
    write_history(
        row("BTC", "Bitcoin", 100.0, "run_1", "2026-10-03T09:00:00"),
        row("BTC", "Bitcoin", 110.0, "run_2", "2026-10-03T10:00:00"),
    )

    trend = trends.build_price_trend(trends.load_history())

    assert list(trend.columns) == config.TREND_COLUMNS


def test_build_price_trend_does_not_mutate_input():
    """The caller's DataFrame must come back untouched."""
    write_history(
        row("BTC", "Bitcoin", 100.0, "run_1", "2026-10-03T09:00:00"),
        row("BTC", "Bitcoin", 110.0, "run_2", "2026-10-03T10:00:00"),
    )
    history = trends.load_history()
    before = history.copy(deep=True)

    trends.build_price_trend(history)

    pd.testing.assert_frame_equal(history, before)


# --- summarize and format ---------------------------------------------------


def test_summarize_counts_each_direction():
    write_history(
        row("UP", "Upcoin", 100.0, "run_1", "2026-10-03T09:00:00"),
        row("DOWN", "Downcoin", 100.0, "run_1", "2026-10-03T09:00:00", rank=2),
        row("FLAT", "Flatcoin", 100.0, "run_1", "2026-10-03T09:00:00", rank=3),
        row("ONE", "Onecoin", 100.0, "run_1", "2026-10-03T09:00:00", rank=4),
        row("UP", "Upcoin", 150.0, "run_2", "2026-10-03T10:00:00"),
        row("DOWN", "Downcoin", 90.0, "run_2", "2026-10-03T10:00:00", rank=2),
        row("FLAT", "Flatcoin", 100.0, "run_2", "2026-10-03T10:00:00", rank=3),
    )

    trend = trends.build_price_trend(trends.load_history())

    assert trends.summarize(trend) == {"up": 1, "down": 1, "flat": 1, "unknown": 1}


def test_format_trend_renders_symbols_and_headers():
    write_history(
        row("BTC", "Bitcoin", 100.0, "run_1", "2026-10-03T09:00:00"),
        row("BTC", "Bitcoin", 110.0, "run_2", "2026-10-03T10:00:00"),
        row("SOL", "Solana", 20.0, "run_1", "2026-10-03T09:00:00", rank=2),
    )

    trend = trends.build_price_trend(trends.load_history())
    text = trends.format_trend(trend)

    assert "Symbol" in text
    assert "Change %" in text
    assert "BTC" in text
    assert "$100.00" in text
    assert "$110.00" in text
    assert "+10.00%" in text
    assert "UP" in text
    # The single-run coin is reported as having no data.
    assert "NO DATA" in text
