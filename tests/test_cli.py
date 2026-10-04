"""CLI-level tests with the scraper mocked (no browser, no network)."""

import pandas as pd
import pytest

from crypto_tracker import cli, config, storage

RAW_ROWS = [
    {"rank": "1", "name": "Bitcoin", "symbol": "BTC", "price": "$64,123.45", "change_24h": "+2.45%", "market_cap": "$1.23T"},
    {"rank": "2", "name": "Ethereum", "symbol": "ETH", "price": "$3,000.00", "change_24h": "-1.20%", "market_cap": "$360.0B"},
    {"rank": "3", "name": "Solana", "symbol": "SOL", "price": "$150.25", "change_24h": "+8.00%", "market_cap": "$70.0B"},
]


@pytest.fixture(autouse=True)
def isolated_dirs(tmp_path, monkeypatch):
    monkeypatch.setattr(config, "DATA_DIR", tmp_path / "data")
    monkeypatch.setattr(config, "SNAPSHOT_DIR", tmp_path / "data" / "snapshots")
    monkeypatch.setattr(config, "HISTORY_DIR", tmp_path / "data" / "history")
    monkeypatch.setattr(config, "LOG_DIR", tmp_path / "logs")
    storage.ensure_directories()
    monkeypatch.setattr(cli, "scrape_top_coins", lambda **kw: list(RAW_ROWS))
    yield


INDEX_ROW = {
    "rank": "", "name": "CoinMarketCap 20 Index DTF", "symbol": "CMC20",
    "price": "$174.93", "change_24h": "0.13%", "market_cap": "$6.27M",
}


def coin_rows(count):
    return [
        {
            "rank": str(i), "name": f"Coin{i}", "symbol": f"C{i}",
            "price": f"${i}.50", "change_24h": "+1.00%", "market_cap": "$1.0B",
        }
        for i in range(1, count + 1)
    ]


def test_output_is_top_ten_when_page_leads_with_an_index_row(monkeypatch):
    """The 10th coin must survive an index product occupying a raw row."""
    rows = [INDEX_ROW] + coin_rows(11)
    monkeypatch.setattr(cli, "scrape_top_coins", lambda **kw: list(rows))

    assert cli.main([]) == 0

    snapshot = pd.read_csv(list(config.SNAPSHOT_DIR.glob("crypto_*.csv"))[0])
    assert len(snapshot) == config.COIN_COUNT
    assert snapshot["rank"].tolist() == list(range(1, config.COIN_COUNT + 1))
    assert "CMC20" not in snapshot["symbol"].tolist()


def test_output_never_exceeds_top_ten(monkeypatch):
    """A page offering far more than 10 coins still yields exactly 10."""
    monkeypatch.setattr(
        cli, "scrape_top_coins", lambda **kw: coin_rows(50)
    )

    assert cli.main([]) == 0

    snapshot = pd.read_csv(list(config.SNAPSHOT_DIR.glob("crypto_*.csv"))[0])
    assert len(snapshot) == config.COIN_COUNT
    assert snapshot["rank"].tolist() == list(range(1, config.COIN_COUNT + 1))


def test_limit_option_is_gone():
    """--limit was removed; argparse must reject it."""
    with pytest.raises(SystemExit) as excinfo:
        cli.main(["--limit", "5"])
    assert excinfo.value.code == 2


def test_help_no_longer_mentions_limit(capsys):
    with pytest.raises(SystemExit):
        cli.main(["--help"])
    assert "--limit" not in capsys.readouterr().out


def test_run_writes_snapshot_and_history(capsys):
    assert cli.main([]) == 0

    snapshots = list(config.SNAPSHOT_DIR.glob("crypto_*.csv"))
    assert len(snapshots) == 1

    snapshot = pd.read_csv(snapshots[0])
    assert list(snapshot.columns) == config.CSV_COLUMNS
    assert len(snapshot) == 3

    history = pd.read_csv(storage.history_path())
    assert len(history) == 3

    out = capsys.readouterr().out
    assert "Bitcoin" in out
    assert "Run ID:" in out


def test_run_applies_price_filter(capsys):
    assert cli.main(["--min-price", "1000"]) == 0

    snapshot = pd.read_csv(list(config.SNAPSHOT_DIR.glob("crypto_*.csv"))[0])
    assert snapshot["symbol"].tolist() == ["BTC", "ETH"]
    assert "Solana" not in capsys.readouterr().out


def test_run_applies_change_filter():
    assert cli.main(["--min-change", "0"]) == 0
    snapshot = pd.read_csv(list(config.SNAPSHOT_DIR.glob("crypto_*.csv"))[0])
    assert snapshot["symbol"].tolist() == ["BTC", "SOL"]


def test_run_no_history_skips_history_file():
    assert cli.main(["--no-history"]) == 0
    assert not storage.history_path().exists()
    assert list(config.SNAPSHOT_DIR.glob("crypto_*.csv"))


def test_filters_removing_everything_writes_no_csv(capsys):
    assert cli.main(["--min-price", "999999999"]) == 0
    assert not list(config.SNAPSHOT_DIR.glob("crypto_*.csv"))
    assert "No rows matched" in capsys.readouterr().out


def test_scraper_error_returns_exit_code_1(monkeypatch):
    from crypto_tracker.scraper import ScraperError

    def boom(**kwargs):
        raise ScraperError("Table not found")

    monkeypatch.setattr(cli, "scrape_top_coins", boom)
    assert cli.main([]) == 1
    assert not list(config.SNAPSHOT_DIR.glob("crypto_*.csv"))


def test_no_valid_rows_returns_exit_code_1(monkeypatch):
    monkeypatch.setattr(cli, "scrape_top_coins", lambda **kw: [])
    assert cli.main([]) == 1
    assert not list(config.SNAPSHOT_DIR.glob("crypto_*.csv"))


def test_consecutive_runs_accumulate_history():
    assert cli.main([]) == 0
    assert cli.main([]) == 0

    history = pd.read_csv(storage.history_path())
    assert len(history) == 6
    assert history["run_id"].nunique() == 2
    assert len(list(config.SNAPSHOT_DIR.glob("crypto_*.csv"))) == 2