from __future__ import annotations

import json
from pathlib import Path
from tempfile import TemporaryDirectory
from unittest import TestCase

from calibre_fixture import create_calibre_fixture_library
from calibre_kobo_companion.config import Settings
from calibre_kobo_companion.db import (
    create_device_token,
    initialize_companion_db,
    revoke_device_token,
)
from calibre_kobo_companion.kobo import SyncToken, encode_sync_token
from calibre_kobo_companion.server import handle_get, handle_post


class ServerTests(TestCase):
    def test_health_endpoint_returns_ok(self) -> None:
        response = handle_get("/health")

        self.assertEqual(response.status, 200)
        self.assertEqual(
            json.loads(json.dumps(response.payload)),
            {
                "service": "calibre-kobo-companion",
                "status": "ok",
            },
        )

    def test_initialization_requires_active_token(self) -> None:
        with TemporaryDirectory() as directory:
            settings = _settings(Path(directory))
            initialize_companion_db(settings.companion_db_path)

            response = handle_get("/kobo/missing/v1/initialization", settings)

        self.assertEqual(response.status, 401)
        self.assertEqual(response.payload, {"error": "unauthorized"})

    def test_initialization_returns_resource_urls_for_active_token(self) -> None:
        with TemporaryDirectory() as directory:
            settings = _settings(Path(directory))
            initialize_companion_db(settings.companion_db_path)
            device_token = create_device_token(settings.companion_db_path, "Clara")

            response = handle_get(
                f"/kobo/{device_token.token}/v1/initialization",
                settings,
            )

        self.assertEqual(response.status, 200)
        resources = response.payload["Resources"]
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

            device_response = handle_post(
                f"/kobo/{device_token.token}/v1/auth/device",
                settings,
            )
            refresh_response = handle_post(
                f"/kobo/{device_token.token}/v1/auth/refresh",
                settings,
            )

        self.assertEqual(device_response.status, 200)
        self.assertEqual(refresh_response.status, 200)
        self.assertEqual(device_response.payload["TokenType"], "Bearer")
        self.assertEqual(
            refresh_response.payload["AccessToken"],
            f"dummy-{device_token.token}",
        )

    def test_revoked_token_is_rejected(self) -> None:
        with TemporaryDirectory() as directory:
            settings = _settings(Path(directory))
            initialize_companion_db(settings.companion_db_path)
            device_token = create_device_token(settings.companion_db_path, "Clara")
            revoke_device_token(settings.companion_db_path, device_token.token)

            response = handle_get(
                f"/kobo/{device_token.token}/v1/initialization",
                settings,
            )

        self.assertEqual(response.status, 401)
        self.assertEqual(response.payload, {"error": "unauthorized"})

    def test_library_sync_returns_fixture_entitlements(self) -> None:
        with TemporaryDirectory() as directory:
            fixture = create_calibre_fixture_library(Path(directory) / "library")
            settings = _settings(Path(directory), library_path=fixture.root)
            initialize_companion_db(settings.companion_db_path)
            device_token = create_device_token(settings.companion_db_path, "Clara")

            response = handle_get(
                f"/kobo/{device_token.token}/v1/library/sync",
                settings,
            )

        self.assertEqual(response.status, 200)
        self.assertIn("x-kobo-synctoken", response.headers)
        self.assertEqual(response.payload["ChangedEntitlements"], [])
        self.assertEqual(response.payload["DeletedEntitlements"], [])
        entitlements = response.payload["NewEntitlements"]
        self.assertEqual(len(entitlements), 2)
        first = entitlements[0]["NewEntitlement"]
        self.assertEqual(first["BookEntitlement"]["Id"], fixture.books[0].uuid)
        self.assertEqual(first["BookMetadata"]["Title"], "Existing Kepub")
        self.assertEqual(first["BookMetadata"]["Language"], "en")
        self.assertEqual(
            first["BookMetadata"]["DownloadUrls"][0]["Url"],
            (
                f"http://example.test/kobo/{device_token.token}"
                f"/download/{fixture.books[0].id}/kepub"
            ),
        )

    def test_library_sync_uses_sync_token_for_incremental_results(self) -> None:
        with TemporaryDirectory() as directory:
            fixture = create_calibre_fixture_library(Path(directory) / "library")
            settings = _settings(Path(directory), library_path=fixture.root)
            initialize_companion_db(settings.companion_db_path)
            device_token = create_device_token(settings.companion_db_path, "Clara")
            sync_token = encode_sync_token(SyncToken(since="2024-02-01 12:00:00+00:00"))

            response = handle_get(
                f"/kobo/{device_token.token}/v1/library/sync",
                settings,
                {"x-kobo-synctoken": sync_token},
            )

        self.assertEqual(response.status, 200)
        entitlements = response.payload["NewEntitlements"]
        self.assertEqual(len(entitlements), 1)
        self.assertEqual(
            entitlements[0]["NewEntitlement"]["BookMetadata"]["Title"],
            "Epub Only",
        )

    def test_library_sync_paginates_results(self) -> None:
        with TemporaryDirectory() as directory:
            fixture = create_calibre_fixture_library(Path(directory) / "library")
            settings = _settings(
                Path(directory),
                library_path=fixture.root,
                kobo_sync_page_size=1,
            )
            initialize_companion_db(settings.companion_db_path)
            device_token = create_device_token(settings.companion_db_path, "Clara")

            first_response = handle_get(
                f"/kobo/{device_token.token}/v1/library/sync",
                settings,
            )
            second_response = handle_get(
                f"/kobo/{device_token.token}/v1/library/sync",
                settings,
                {"x-kobo-synctoken": first_response.headers["x-kobo-synctoken"]},
            )

        self.assertEqual(first_response.status, 200)
        self.assertEqual(first_response.headers["x-kobo-sync"], "continue")
        self.assertEqual(len(first_response.payload["NewEntitlements"]), 1)
        self.assertEqual(second_response.status, 200)
        self.assertNotIn("x-kobo-sync", second_response.headers)
        self.assertEqual(len(second_response.payload["NewEntitlements"]), 1)
        self.assertNotEqual(
            first_response.payload["NewEntitlements"][0]["NewEntitlement"][
                "BookMetadata"
            ]["Id"],
            second_response.payload["NewEntitlements"][0]["NewEntitlement"][
                "BookMetadata"
            ]["Id"],
        )

    def test_book_metadata_returns_single_book(self) -> None:
        with TemporaryDirectory() as directory:
            fixture = create_calibre_fixture_library(Path(directory) / "library")
            settings = _settings(Path(directory), library_path=fixture.root)
            initialize_companion_db(settings.companion_db_path)
            device_token = create_device_token(settings.companion_db_path, "Clara")

            response = handle_get(
                (
                    f"/kobo/{device_token.token}/v1/library/"
                    f"{fixture.books[1].uuid}/metadata"
                ),
                settings,
            )

        self.assertEqual(response.status, 200)
        self.assertEqual(response.payload["Id"], fixture.books[1].uuid)
        self.assertEqual(response.payload["Title"], "Epub Only")
        self.assertEqual(response.payload["Contributors"], [{"Name": "Grace Hopper"}])

    def test_book_metadata_returns_not_found_for_unknown_uuid(self) -> None:
        with TemporaryDirectory() as directory:
            fixture = create_calibre_fixture_library(Path(directory) / "library")
            settings = _settings(Path(directory), library_path=fixture.root)
            initialize_companion_db(settings.companion_db_path)
            device_token = create_device_token(settings.companion_db_path, "Clara")

            response = handle_get(
                f"/kobo/{device_token.token}/v1/library/missing/metadata",
                settings,
            )

        self.assertEqual(response.status, 404)
        self.assertEqual(response.payload, {"error": "not_found"})


def _settings(
    directory: Path,
    *,
    library_path: Path | None = None,
    kobo_sync_page_size: int = 100,
) -> Settings:
    return Settings(
        calibre_library_path=library_path or directory / "library",
        companion_db_path=directory / "companion.db",
        companion_cache_path=directory / "cache",
        public_base_url="http://example.test",
        kobo_sync_page_size=kobo_sync_page_size,
    )
