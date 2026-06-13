from __future__ import annotations

import json
from pathlib import Path
from tempfile import TemporaryDirectory
from unittest import TestCase

from calibre_kobo_companion.config import Settings
from calibre_kobo_companion.db import (
    create_device_token,
    initialize_companion_db,
    revoke_device_token,
)
from calibre_kobo_companion.server import handle_get, handle_post


class ServerTests(TestCase):
    def test_health_endpoint_returns_ok(self) -> None:
        status, body = handle_get("/health")

        self.assertEqual(status, 200)
        self.assertEqual(
            json.loads(json.dumps(body)),
            {
                "service": "calibre-kobo-companion",
                "status": "ok",
            },
        )

    def test_initialization_requires_active_token(self) -> None:
        with TemporaryDirectory() as directory:
            settings = _settings(Path(directory))
            initialize_companion_db(settings.companion_db_path)

            status, body = handle_get("/kobo/missing/v1/initialization", settings)

        self.assertEqual(status, 401)
        self.assertEqual(body, {"error": "unauthorized"})

    def test_initialization_returns_resource_urls_for_active_token(self) -> None:
        with TemporaryDirectory() as directory:
            settings = _settings(Path(directory))
            initialize_companion_db(settings.companion_db_path)
            device_token = create_device_token(settings.companion_db_path, "Clara")

            status, body = handle_get(
                f"/kobo/{device_token.token}/v1/initialization",
                settings,
            )

        self.assertEqual(status, 200)
        resources = body["Resources"]
        self.assertEqual(
            resources["LibrarySync"],
            f"http://example.test/kobo/{device_token.token}/v1/library/sync",
        )
        self.assertEqual(
            resources["AuthDevice"],
            f"http://example.test/kobo/{device_token.token}/v1/auth/device",
        )

    def test_auth_device_and_refresh_return_dummy_tokens(self) -> None:
        with TemporaryDirectory() as directory:
            settings = _settings(Path(directory))
            initialize_companion_db(settings.companion_db_path)
            device_token = create_device_token(settings.companion_db_path, "Clara")

            device_status, device_body = handle_post(
                f"/kobo/{device_token.token}/v1/auth/device",
                settings,
            )
            refresh_status, refresh_body = handle_post(
                f"/kobo/{device_token.token}/v1/auth/refresh",
                settings,
            )

        self.assertEqual(device_status, 200)
        self.assertEqual(refresh_status, 200)
        self.assertEqual(device_body["TokenType"], "Bearer")
        self.assertEqual(refresh_body["AccessToken"], f"dummy-{device_token.token}")

    def test_revoked_token_is_rejected(self) -> None:
        with TemporaryDirectory() as directory:
            settings = _settings(Path(directory))
            initialize_companion_db(settings.companion_db_path)
            device_token = create_device_token(settings.companion_db_path, "Clara")
            revoke_device_token(settings.companion_db_path, device_token.token)

            status, body = handle_get(
                f"/kobo/{device_token.token}/v1/initialization",
                settings,
            )

        self.assertEqual(status, 401)
        self.assertEqual(body, {"error": "unauthorized"})


def _settings(directory: Path) -> Settings:
    return Settings(
        calibre_library_path=directory / "library",
        companion_db_path=directory / "companion.db",
        companion_cache_path=directory / "cache",
        public_base_url="http://example.test",
    )
