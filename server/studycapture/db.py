from __future__ import annotations

import json
import sqlite3
import threading
from contextlib import contextmanager
from pathlib import Path
from typing import Any, Iterator


SCHEMA = """
PRAGMA journal_mode=WAL;
PRAGMA foreign_keys=ON;
CREATE TABLE IF NOT EXISTS sessions (
  id TEXT PRIMARY KEY,
  title TEXT NOT NULL,
  url TEXT NOT NULL,
  folder TEXT NOT NULL,
  language TEXT,
  status TEXT NOT NULL,
  transcription_status TEXT NOT NULL DEFAULT 'aguardando',
  created_at TEXT NOT NULL,
  finished_at TEXT,
  total_samples INTEGER NOT NULL DEFAULT 0,
  expected_last_sequence INTEGER,
  note_path TEXT,
  error TEXT
);
CREATE TABLE IF NOT EXISTS blocks (
  session_id TEXT NOT NULL REFERENCES sessions(id) ON DELETE CASCADE,
  sequence INTEGER NOT NULL,
  position_samples INTEGER NOT NULL,
  sample_count INTEGER NOT NULL,
  checksum TEXT NOT NULL,
  file_path TEXT NOT NULL,
  created_at TEXT NOT NULL,
  PRIMARY KEY (session_id, sequence)
);
CREATE TABLE IF NOT EXISTS tasks (
  id INTEGER PRIMARY KEY AUTOINCREMENT,
  session_id TEXT NOT NULL REFERENCES sessions(id) ON DELETE CASCADE,
  kind TEXT NOT NULL,
  status TEXT NOT NULL DEFAULT 'aguardando',
  attempts INTEGER NOT NULL DEFAULT 0,
  next_run_at TEXT NOT NULL,
  error TEXT,
  UNIQUE(session_id, kind)
);
CREATE TABLE IF NOT EXISTS windows (
  session_id TEXT NOT NULL REFERENCES sessions(id) ON DELETE CASCADE,
  window_index INTEGER NOT NULL,
  start_sample INTEGER NOT NULL,
  end_sample INTEGER NOT NULL,
  status TEXT NOT NULL DEFAULT 'aguardando',
  response_json TEXT,
  segments_json TEXT,
  error TEXT,
  PRIMARY KEY(session_id, window_index)
);
"""


class Database:
    def __init__(self, path: Path):
        self.path = path
        self._lock = threading.RLock()
        self.path.parent.mkdir(parents=True, exist_ok=True)
        with self.connect() as conn:
            conn.executescript(SCHEMA)

    def connect(self) -> sqlite3.Connection:
        conn = sqlite3.connect(self.path, check_same_thread=False)
        conn.row_factory = sqlite3.Row
        conn.execute("PRAGMA foreign_keys=ON")
        return conn

    @contextmanager
    def tx(self) -> Iterator[sqlite3.Connection]:
        with self._lock:
            conn = self.connect()
            try:
                yield conn
                conn.commit()
            except Exception:
                conn.rollback()
                raise
            finally:
                conn.close()

    @staticmethod
    def row(row: sqlite3.Row | None) -> dict[str, Any] | None:
        return dict(row) if row else None

    @staticmethod
    def decode(row: dict[str, Any]) -> dict[str, Any]:
        for key in ("response_json", "segments_json"):
            if row.get(key):
                row[key] = json.loads(row[key])
        return row
