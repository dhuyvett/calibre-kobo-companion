from __future__ import annotations

from contextlib import closing
from dataclasses import dataclass
from datetime import UTC, datetime
import sqlite3
from pathlib import Path
import secrets


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
    with closing(sqlite3.connect(path)) as connection:
        connection.executescript(SCHEMA)
        connection.commit()


@dataclass(frozen=True)
class DeviceToken:
    token: str
    label: str | None
    created_at: str
    revoked_at: str | None


def create_device_token(path: Path, label: str | None = None) -> DeviceToken:
    token = secrets.token_urlsafe(32)
    created_at = _utc_now()
    with closing(sqlite3.connect(path)) as connection:
        connection.execute(
            """
            INSERT INTO device_tokens (token, label, created_at, revoked_at)
            VALUES (?, ?, ?, NULL)
            """,
            (token, label, created_at),
        )
        connection.commit()

    return DeviceToken(
        token=token,
        label=label,
        created_at=created_at,
        revoked_at=None,
    )


def list_device_tokens(path: Path) -> tuple[DeviceToken, ...]:
    with closing(sqlite3.connect(path)) as connection:
        rows = connection.execute(
            """
            SELECT token, label, created_at, revoked_at
            FROM device_tokens
            ORDER BY created_at, token
            """
        ).fetchall()

    return tuple(DeviceToken(*row) for row in rows)


def revoke_device_token(path: Path, token: str) -> bool:
    revoked_at = _utc_now()
    with closing(sqlite3.connect(path)) as connection:
        cursor = connection.execute(
            """
            UPDATE device_tokens
            SET revoked_at = ?
            WHERE token = ? AND revoked_at IS NULL
            """,
            (revoked_at, token),
        )
        connection.commit()
        return cursor.rowcount > 0


def is_device_token_active(path: Path, token: str) -> bool:
    with closing(sqlite3.connect(path)) as connection:
        row = connection.execute(
            """
            SELECT 1
            FROM device_tokens
            WHERE token = ? AND revoked_at IS NULL
            """,
            (token,),
        ).fetchone()
    return row is not None


def _utc_now() -> str:
    return datetime.now(UTC).isoformat(timespec="seconds")
