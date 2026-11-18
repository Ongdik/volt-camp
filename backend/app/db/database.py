"""SQLite 연결과 스키마. 하나의 연결을 잠금으로 보호해 FastAPI 스레드에서 함께 쓴다."""

from __future__ import annotations

import sqlite3
import threading
from contextlib import contextmanager
from pathlib import Path
from typing import Iterator

SCHEMA = """
CREATE TABLE IF NOT EXISTS run_events (
    id INTEGER PRIMARY KEY AUTOINCREMENT,
    event_id TEXT NOT NULL UNIQUE,
    session_id TEXT NOT NULL,
    turn_id TEXT NOT NULL,
    seq INTEGER NOT NULL,
    ts TEXT NOT NULL,
    type TEXT NOT NULL,
    node TEXT,
    payload_json TEXT NOT NULL
);
CREATE INDEX IF NOT EXISTS idx_run_events_session ON run_events(session_id, id);

CREATE TABLE IF NOT EXISTS plans (
    id INTEGER PRIMARY KEY AUTOINCREMENT,
    title TEXT NOT NULL,
    campsite_json TEXT NOT NULL,
    origin TEXT,
    start_at TEXT NOT NULL,
    end_at TEXT NOT NULL,
    vehicle_json TEXT NOT NULL,
    devices_json TEXT NOT NULL,
    charge_stop_json TEXT,
    expected_soc_json TEXT,
    evidence_json TEXT,
    memo TEXT NOT NULL DEFAULT '',
    session_id TEXT,
    created_at TEXT NOT NULL,
    updated_at TEXT NOT NULL
);
CREATE INDEX IF NOT EXISTS idx_plans_period ON plans(start_at, end_at);

CREATE TABLE IF NOT EXISTS anomalies (
    id INTEGER PRIMARY KEY AUTOINCREMENT,
    session_id TEXT NOT NULL,
    turn_id TEXT NOT NULL UNIQUE,
    verdict TEXT NOT NULL,
    findings_json TEXT NOT NULL,
    diagnosis TEXT,
    diagnosis_status TEXT NOT NULL DEFAULT 'NONE',
    created_at TEXT NOT NULL
);
CREATE INDEX IF NOT EXISTS idx_anomalies_session ON anomalies(session_id, id);
"""


class Database:
    def __init__(self, path: str = ":memory:"):
        if path != ":memory:":
            Path(path).parent.mkdir(parents=True, exist_ok=True)
        self._conn = sqlite3.connect(path, check_same_thread=False)
        self._conn.row_factory = sqlite3.Row
        self._lock = threading.RLock()
        self.executescript(SCHEMA)

    def executescript(self, script: str) -> None:
        with self._lock:
            self._conn.executescript(script)
            self._conn.commit()

    @contextmanager
    def tx(self) -> Iterator[sqlite3.Connection]:
        with self._lock:
            try:
                yield self._conn
                self._conn.commit()
            except Exception:
                self._conn.rollback()
                raise

    def close(self) -> None:
        with self._lock:
            self._conn.close()
