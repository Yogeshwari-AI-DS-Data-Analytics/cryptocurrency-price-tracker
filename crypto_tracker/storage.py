"""Storage layer: writes CSV files under data/.

Two files are produced per run:

* ``data/snapshots/crypto_<run_id>.csv`` - one run, wide format.
* ``data/history/crypto_history.csv``    - every run appended, for trend analysis.
"""

import logging
from pathlib import Path
from typing import Optional

import pandas as pd

from . import config

logger = logging.getLogger(__name__)


class StorageError(Exception):
    """Raised when a CSV file cannot be written."""


def ensure_directories() -> None:
    """Create data/ and logs/ if they are missing."""
    for directory in (
        config.DATA_DIR,
        config.SNAPSHOT_DIR,
        config.HISTORY_DIR,
        config.LOG_DIR,
    ):
        directory.mkdir(parents=True, exist_ok=True)


def snapshot_path(run_id: str) -> Path:
    """Path of the snapshot CSV for one run."""
    return config.SNAPSHOT_DIR / f"crypto_{run_id}.csv"


def history_path() -> Path:
    """Path of the appended history CSV."""
    return config.HISTORY_DIR / config.HISTORY_FILENAME


def _write(frame: pd.DataFrame, path: Path, mode: str = "w") -> None:
    try:
        frame.to_csv(
            path,
            mode=mode,
            index=False,
            header=(mode == "w"),
            encoding="utf-8",
        )
    except OSError as exc:
        raise StorageError(f"Could not write {path}: {exc}") from exc


def save_snapshot(frame: pd.DataFrame, run_id: str) -> Path:
    """Write the one-file-per-run snapshot."""
    if frame.empty:
        raise StorageError("Refusing to write an empty snapshot file.")

    path = snapshot_path(run_id)
    _write(frame, path)
    logger.info("Snapshot saved: %s", path)
    return path


def append_history(frame: pd.DataFrame, run_id: str) -> Path:
    """Append this run to the history CSV, replacing any prior rows for it.

    De-duplicating on ``run_id`` means re-running with the same run ID (or a
    retry after a partial write) cannot duplicate history entries.
    """
    if frame.empty:
        raise StorageError("Refusing to append an empty frame to history.")

    path = history_path()
    history = pd.DataFrame(columns=config.CSV_COLUMNS)

    if path.exists():
        try:
            existing = pd.read_csv(path)
        except (OSError, pd.errors.ParserError, pd.errors.EmptyDataError) as exc:
            # Never destroy data: move the unreadable file aside.
            backup = path.with_suffix(".corrupt.csv")
            path.rename(backup)
            logger.error(
                "History file was unreadable (%s); moved to %s", exc, backup
            )
        else:
            missing = [c for c in config.CSV_COLUMNS if c not in existing.columns]
            if missing:
                # The file parsed as CSV but is not our history file, so it
                # would corrupt the data set. Preserve it and start fresh.
                backup = path.with_suffix(".corrupt.csv")
                path.rename(backup)
                logger.error(
                    "History file is missing expected column(s) %s; moved to %s",
                    ", ".join(missing),
                    backup,
                )
            elif not existing.empty:
                history = existing[existing["run_id"] != run_id]

    combined = pd.concat([history, frame], ignore_index=True) if not history.empty else frame
    _write(combined[config.CSV_COLUMNS], path)
    logger.info(
        "History updated: %s (total rows=%s)", path, len(combined)
    )
    return path


def unique_run_id(base: str) -> str:
    """Return ``base``, or ``base_2``, ``base_3``... if that run ID is taken.

    Two runs started in the same second would otherwise share a run ID, and
    the second would overwrite the first snapshot and deduplicate its history
    rows away.
    """
    if not snapshot_path(base).exists():
        return base

    suffix = 2
    while snapshot_path(f"{base}_{suffix}").exists():
        suffix += 1
    return f"{base}_{suffix}"


def latest_snapshot() -> Optional[Path]:
    """Most recently written snapshot, or None if there are none."""
    files = sorted(config.SNAPSHOT_DIR.glob("crypto_*.csv"))
    return files[-1] if files else None