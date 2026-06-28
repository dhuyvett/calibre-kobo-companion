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
            "KOBO_SYNC_MODE": "hybrid",
            "KOBO_STORE_API_URL": "https://store.example.test/",
            "KOBO_PROXY_TIMEOUT_SECONDS": "12",
            "HYBRID_SYNC_REQUIRE_LOCAL_LIBRARY": "true",
            "HYBRID_STUB_NONESSENTIAL_KOBO": "true",
            "ENABLE_KEPUBIFY": "true",
            "KEPUBIFY_PATH": "/usr/local/bin/kepubify",
            "TLS_CERT_PATH": "/tmp/tls/fullchain.pem",
            "TLS_KEY_PATH": "/tmp/tls/privkey.pem",
        }

        with patch.dict(os.environ, env, clear=True):
            settings = load_settings()

        self.assertEqual(settings.calibre_library_path, Path("/tmp/library"))
        self.assertEqual(settings.companion_db_path, Path("/tmp/config/companion.db"))
        self.assertEqual(settings.companion_cache_path, Path("/tmp/cache"))
        self.assertEqual(settings.public_base_url, "http://example.test")
        self.assertEqual(settings.listen_port, 9090)
        self.assertEqual(settings.kobo_sync_mode, "hybrid")
        self.assertEqual(settings.kobo_store_api_url, "https://store.example.test")
        self.assertEqual(settings.kobo_proxy_timeout_seconds, 12)
        self.assertTrue(settings.hybrid_sync_require_local_library)
        self.assertTrue(settings.hybrid_stub_nonessential_kobo)
        self.assertTrue(settings.enable_kepubify)
        self.assertEqual(settings.kepubify_path, Path("/usr/local/bin/kepubify"))
        self.assertTrue(settings.tls_enabled)
        self.assertEqual(settings.tls_cert_path, Path("/tmp/tls/fullchain.pem"))
        self.assertEqual(settings.tls_key_path, Path("/tmp/tls/privkey.pem"))

    def test_tls_cert_and_key_must_be_configured_together(self) -> None:
        env = {
            "CALIBRE_LIBRARY_PATH": "/tmp/library",
            "TLS_CERT_PATH": "/tmp/tls/fullchain.pem",
        }

        with patch.dict(os.environ, env, clear=True):
            with self.assertRaisesRegex(ConfigError, "TLS_CERT_PATH and TLS_KEY_PATH"):
                load_settings()

    def test_kobo_sync_mode_must_be_valid(self) -> None:
        env = {
            "CALIBRE_LIBRARY_PATH": "/tmp/library",
            "KOBO_SYNC_MODE": "invalid",
        }

        with patch.dict(os.environ, env, clear=True):
            with self.assertRaisesRegex(ConfigError, "KOBO_SYNC_MODE"):
                load_settings()
