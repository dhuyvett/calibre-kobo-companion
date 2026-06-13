from __future__ import annotations

import sqlite3
from pathlib import Path


SCHEMA = """
CREATE TABLE IF NOT EXISTS device_tokens (
  token TEXT PRIMARY KEY,
  label TEXT,
  created_at TEXT NOT NULL,
  revoked_at TEXT
);

CREATE TABLE IF NOT EXISTS sync_devices (
  token TEXT PRIMARY KEY,
  last_seen_at TEXT,
  FOREIGN KEY(token) REFERENCES device_tokens(token)
);
"""


def initialize_companion_db(path: Path) -> None:
    path.parent.mkdir(parents=True, exist_ok=True)
    with sqlite3.connect(path) as connection:
        connection.executescript(SCHEMA)
        connection.commit()
