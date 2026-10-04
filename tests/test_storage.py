"""Unit tests for CSV writing (no browser required)."""

import pandas as pd
import pytest

from crypto_tracker import config, dataframe, storage


@pytest.fixture()
def sample_frame():
    raw = [
        {"rank": "1", "name": "Bitcoin", "symbol": "BTC", "price": "$64,123.45", "change_24h": "+2.45%", "market_cap": "$1.23T"},
        {"rank": "2", "name": "Ethereum", "symbol": "ETH", "price": "$3,000.00", "change_24h": "-1.20%", "market_cap": "$360.0B"},
    ]
    return dataframe.add_metadata(dataframe.build_dataframe(raw), run_id="20261002_141530")


@pytest.fixture(autouse=True)
def temp_dirs(tmp_path, monkeypatch):
    monkeypatch.setattr(config, "DATA_DIR", tmp_path / "data")
    monkeypatch.setattr(config, "SNAPSHOT_DIR", tmp_path / "data" / "snapshots")
    monkeypatch.setattr(config, "HISTORY_DIR", tmp_path / "data" / "history")
    monkeypatch.setattr(config, "LOG_DIR", tmp_path / "logs")
    storage.ensure_directories()
    yield


def test_ensure_directories_creates_folders():
    assert config.SNAPSHOT_DIR.exists()
    assert config.HISTORY_DIR.exists()


def test_save_snapshot_writes_expected_file(sample_frame):
    path = storage.save_snapshot(sample_frame, "20261002_141530")
    assert path.name == "crypto_20261002_141530.csv"

    written = pd.read_csv(path)
    assert list(written.columns) == config.CSV_COLUMNS
    assert len(written) == 2


def test_save_snapshot_refuses_empty_frame():
    empty = pd.DataFrame(columns=config.CSV_COLUMNS)
    with pytest.raises(storage.StorageError):
        storage.save_snapshot(empty, "20261002_141530")


def test_append_history_accumulates_runs(sample_frame):
    storage.append_history(sample_frame, "20261002_141530")

    second = sample_frame.copy()
    second["run_id"] = "20261002_150000"
    storage.append_history(second, "20261002_150000")

    history = pd.read_csv(storage.history_path())
    assert len(history) == 4
    assert history["run_id"].nunique() == 2


def test_append_history_is_idempotent_for_same_run(sample_frame):
    storage.append_history(sample_frame, "20261002_141530")
    storage.append_history(sample_frame, "20261002_141530")

    history = pd.read_csv(storage.history_path())
    assert len(history) == 2


def test_append_history_recovers_from_corrupt_file(sample_frame):
    storage.history_path().write_text("this is not,a,valid\ncsv file", encoding="utf-8")

    storage.append_history(sample_frame, "20261002_141530")

    history = pd.read_csv(storage.history_path())
    assert len(history) == 2
    assert storage.history_path().with_suffix(".corrupt.csv").exists()


def test_unique_run_id_avoids_collisions(tmp_path, sample_frame):
    assert storage.unique_run_id("20261002_141530") == "20261002_141530"
    storage.save_snapshot(sample_frame, "20261002_141530")
    assert storage.unique_run_id("20261002_141530") == "20261002_141530_2"
    storage.save_snapshot(sample_frame, "20261002_141530_2")
    assert storage.unique_run_id("20261002_141530") == "20261002_141530_3"


def test_latest_snapshot_returns_newest(tmp_path, sample_frame):
    assert storage.latest_snapshot() is None
    storage.save_snapshot(sample_frame, "20261002_141530")
    storage.save_snapshot(sample_frame, "20261002_150000")
    assert storage.latest_snapshot().name == "crypto_20261002_150000.csv"