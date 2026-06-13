from __future__ import annotations

import os
from pathlib import Path
from unittest import TestCase
from unittest.mock import patch

from calibre_kobo_companion.config import ConfigError, load_settings


class ConfigTests(TestCase):
    def test_load_settings_requires_library_path(self) -> None:
        with patch.dict(os.environ, {}, clear=True):
            with self.assertRaisesRegex(ConfigError, "CALIBRE_LIBRARY_PATH"):
                load_settings()

    def test_load_settings_reads_environment(self) -> None:
        env = {
            "CALIBRE_LIBRARY_PATH": "/tmp/library",
            "COMPANION_DB_PATH": "/tmp/config/companion.db",
            "COMPANION_CACHE_PATH": "/tmp/cache",
            "PUBLIC_BASE_URL": "http://example.test/",
            "LISTEN_PORT": "9090",
            "ENABLE_KEPUBIFY": "true",
            "KEPUBIFY_PATH": "/usr/local/bin/kepubify",
        }

        with patch.dict(os.environ, env, clear=True):
            settings = load_settings()

        self.assertEqual(settings.calibre_library_path, Path("/tmp/library"))
        self.assertEqual(settings.companion_db_path, Path("/tmp/config/companion.db"))
        self.assertEqual(settings.companion_cache_path, Path("/tmp/cache"))
        self.assertEqual(settings.public_base_url, "http://example.test")
        self.assertEqual(settings.listen_port, 9090)
        self.assertTrue(settings.enable_kepubify)
        self.assertEqual(settings.kepubify_path, Path("/usr/local/bin/kepubify"))
