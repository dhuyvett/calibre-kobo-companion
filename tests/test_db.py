from __future__ import annotations

from contextlib import closing
import sqlite3
from pathlib import Path
from tempfile import TemporaryDirectory
from unittest import TestCase

from calibre_kobo_companion.db import (
    create_device_token,
    initialize_companion_db,
    is_device_token_active,
    list_device_tokens,
    revoke_device_token,
)


class CompanionDbTests(TestCase):
    def test_initialize_companion_db_creates_schema(self) -> None:
        with TemporaryDirectory() as directory:
            db_path = Path(directory) / "config" / "companion.db"

            initialize_companion_db(db_path)

            with closing(sqlite3.connect(db_path)) as connection:
                tables = {
                    row[0]
                    for row in connection.execute(
                        "SELECT name FROM sqlite_master WHERE type = 'table'"
                    )
                }

        self.assertIn("device_tokens", tables)
        self.assertIn("sync_devices", tables)

    def test_device_token_lifecycle(self) -> None:
        with TemporaryDirectory() as directory:
            db_path = Path(directory) / "companion.db"
            initialize_companion_db(db_path)

            device_token = create_device_token(db_path, "Clara BW")
            tokens = list_device_tokens(db_path)

            self.assertEqual(len(tokens), 1)
            self.assertEqual(tokens[0].token, device_token.token)
            self.assertEqual(tokens[0].label, "Clara BW")
            self.assertIsNone(tokens[0].revoked_at)
            self.assertTrue(is_device_token_active(db_path, device_token.token))

            self.assertTrue(revoke_device_token(db_path, device_token.token))
            self.assertFalse(is_device_token_active(db_path, device_token.token))
            self.assertFalse(revoke_device_token(db_path, device_token.token))
