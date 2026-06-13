from __future__ import annotations

import sqlite3
from pathlib import Path
from tempfile import TemporaryDirectory
from unittest import TestCase

from calibre_kobo_companion.db import initialize_companion_db


class CompanionDbTests(TestCase):
    def test_initialize_companion_db_creates_schema(self) -> None:
        with TemporaryDirectory() as directory:
            db_path = Path(directory) / "config" / "companion.db"

            initialize_companion_db(db_path)

            with sqlite3.connect(db_path) as connection:
                tables = {
                    row[0]
                    for row in connection.execute(
                        "SELECT name FROM sqlite_master WHERE type = 'table'"
                    )
                }

        self.assertIn("device_tokens", tables)
        self.assertIn("sync_devices", tables)
