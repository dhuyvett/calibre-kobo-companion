from __future__ import annotations

import base64
import json
from pathlib import Path
from tempfile import TemporaryDirectory
from unittest import TestCase

from calibre_fixture import SMALL_JPEG, create_calibre_fixture_library
from calibre_kobo_companion.config import Settings
from calibre_kobo_companion.db import (
    create_device_token,
    initialize_companion_db,
    revoke_device_token,
)
from calibre_kobo_companion.kobo import SyncToken, encode_sync_token
from calibre_kobo_companion.server import handle_delete, handle_get, handle_post


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
        self.assertEqual(
            resources["library_sync"],
            f"http://example.test/kobo/{device_token.token}/v1/library/sync",
        )
        self.assertEqual(
            resources["device_auth"],
            f"http://example.test/kobo/{device_token.token}/v1/auth/device",
        )
        self.assertEqual(
            resources["image_url_template"],
            (
                f"http://example.test/kobo/{device_token.token}"
                "/{ImageId}/{Width}/{Height}/false/image.jpg"
            ),
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

    def test_compatibility_endpoints_return_success(self) -> None:
        with TemporaryDirectory() as directory:
            settings = _settings(Path(directory))
            initialize_companion_db(settings.companion_db_path)
            device_token = create_device_token(settings.companion_db_path, "Clara")

            profile_response = handle_get(
                f"/kobo/{device_token.token}/v1/user/profile",
                settings,
            )
            assets_response = handle_get(
                f"/kobo/{device_token.token}/v1/assets",
                settings,
            )
            analytics_response = handle_post(
                f"/kobo/{device_token.token}/v1/analytics/gettests",
                settings,
            )
            affiliate_response = handle_get(
                (
                    f"/kobo/{device_token.token}/v1/affiliate"
                    "?PlatformID=00000000-0000-0000-0000-000000000388"
                ),
                settings,
            )
            deals_response = handle_get(
                f"/kobo/{device_token.token}/v1/deals",
                settings,
            )
            featured_products_response = handle_get(
                f"/kobo/{device_token.token}/v1/products/featured/",
                settings,
            )

        self.assertEqual(profile_response.status, 200)
        self.assertEqual(assets_response.status, 200)
        self.assertEqual(analytics_response.status, 200)
        self.assertEqual(affiliate_response.status, 200)
        self.assertEqual(deals_response.status, 200)
        self.assertEqual(featured_products_response.status, 200)
        self.assertEqual(analytics_response.payload["Result"], "Success")

    def test_read_only_kobo_mutations_are_acknowledged(self) -> None:
        with TemporaryDirectory() as directory:
            settings = _settings(Path(directory))
            initialize_companion_db(settings.companion_db_path)
            device_token = create_device_token(settings.companion_db_path, "Clara")

            delete_book_response = handle_delete(
                (
                    f"/kobo/{device_token.token}/v1/library/"
                    "d65b0a72-704c-4822-9605-ad837e66fc17"
                ),
                settings,
            )
            tag_delete_response = handle_post(
                (
                    f"/kobo/{device_token.token}/v1/library/tags/"
                    "36791d37-3548-4fa5-981e-4529e1cf6fc5/items/delete"
                ),
                settings,
            )

        self.assertEqual(delete_book_response.status, 200)
        self.assertEqual(delete_book_response.payload, {})
        self.assertEqual(tag_delete_response.status, 200)
        self.assertEqual(tag_delete_response.payload, {})

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
        entitlements = response.payload
        self.assertEqual(len(entitlements), 2)
        first = entitlements[0]["NewEntitlement"]
        self.assertEqual(first["BookEntitlement"]["Id"], fixture.books[0].uuid)
        self.assertEqual(first["BookMetadata"]["Title"], "Existing Kepub")
        self.assertEqual(first["BookMetadata"]["Language"], "en")
        self.assertEqual(first["BookMetadata"]["Contributors"], ["Ada Lovelace"])
        self.assertEqual(
            first["BookMetadata"]["ContributorRoles"],
            [{"Name": "Ada Lovelace"}],
        )
        self.assertEqual(
            first["BookMetadata"]["CurrentDisplayPrice"],
            {"CurrencyCode": "USD", "TotalAmount": 0},
        )
        self.assertEqual(
            first["BookMetadata"]["Categories"],
            ["00000000-0000-0000-0000-000000000001"],
        )
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
        entitlements = response.payload
        self.assertEqual(len(entitlements), 1)
        self.assertEqual(
            entitlements[0]["NewEntitlement"]["BookMetadata"]["Title"],
            "Epub Only",
        )

    def test_library_sync_ignores_legacy_unversioned_sync_token(self) -> None:
        with TemporaryDirectory() as directory:
            fixture = create_calibre_fixture_library(Path(directory) / "library")
            settings = _settings(Path(directory), library_path=fixture.root)
            initialize_companion_db(settings.companion_db_path)
            device_token = create_device_token(settings.companion_db_path, "Clara")
            legacy_payload = json.dumps(
                {"since": "2024-02-02 12:00:00+00:00", "offset": 0},
                separators=(",", ":"),
                sort_keys=True,
            ).encode("utf-8")
            legacy_sync_token = (
                base64.urlsafe_b64encode(legacy_payload).decode("ascii").rstrip("=")
            )

            response = handle_get(
                f"/kobo/{device_token.token}/v1/library/sync",
                settings,
                {"x-kobo-synctoken": legacy_sync_token},
            )

        self.assertEqual(response.status, 200)
        self.assertEqual(len(response.payload), 2)

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
        self.assertEqual(len(first_response.payload), 1)
        self.assertEqual(second_response.status, 200)
        self.assertNotIn("x-kobo-sync", second_response.headers)
        self.assertEqual(len(second_response.payload), 1)
        self.assertNotEqual(
            first_response.payload[0]["NewEntitlement"]["BookMetadata"]["Id"],
            second_response.payload[0]["NewEntitlement"]["BookMetadata"]["Id"],
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
        metadata = response.payload[0]
        self.assertEqual(metadata["Id"], fixture.books[1].uuid)
        self.assertEqual(metadata["Title"], "Epub Only")
        self.assertEqual(metadata["Contributors"], ["Grace Hopper"])
        self.assertEqual(metadata["ContributorRoles"], [{"Name": "Grace Hopper"}])
        self.assertEqual(
            [download["Format"] for download in metadata["DownloadUrls"]],
            ["EPUB3", "EPUB"],
        )
        self.assertEqual(
            {download["Platform"] for download in metadata["DownloadUrls"]},
            {"Generic"},
        )

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

    def test_download_streams_existing_epub_file(self) -> None:
        with TemporaryDirectory() as directory:
            fixture = create_calibre_fixture_library(Path(directory) / "library")
            settings = _settings(Path(directory), library_path=fixture.root)
            initialize_companion_db(settings.companion_db_path)
            device_token = create_device_token(settings.companion_db_path, "Clara")

            response = handle_get(
                f"/kobo/{device_token.token}/download/{fixture.books[1].id}/epub",
                settings,
            )
            self.assertIsNotNone(response.file_path)
            response_body = response.file_path.read_bytes()

        self.assertEqual(response.status, 200)
        self.assertEqual(response.content_type, "application/epub+zip")
        self.assertEqual(response_body, b"Epub Only fixture EPUB\n")
        self.assertEqual(response.headers["X-Content-Type-Options"], "nosniff")

    def test_download_prefers_exact_requested_format(self) -> None:
        with TemporaryDirectory() as directory:
            fixture = create_calibre_fixture_library(Path(directory) / "library")
            settings = _settings(Path(directory), library_path=fixture.root)
            initialize_companion_db(settings.companion_db_path)
            device_token = create_device_token(settings.companion_db_path, "Clara")

            response = handle_get(
                f"/kobo/{device_token.token}/download/{fixture.books[0].id}/kepub",
                settings,
            )
            self.assertIsNotNone(response.file_path)
            response_body = response.file_path.read_bytes()

        self.assertEqual(response.status, 200)
        self.assertEqual(response.content_type, "application/vnd.kobo.kepub+zip")
        self.assertEqual(response_body, b"Existing Kepub fixture KEPUB\n")

    def test_download_returns_not_found_for_missing_format(self) -> None:
        with TemporaryDirectory() as directory:
            fixture = create_calibre_fixture_library(Path(directory) / "library")
            settings = _settings(Path(directory), library_path=fixture.root)
            initialize_companion_db(settings.companion_db_path)
            device_token = create_device_token(settings.companion_db_path, "Clara")

            response = handle_get(
                f"/kobo/{device_token.token}/download/{fixture.books[1].id}/kepub",
                settings,
            )

        self.assertEqual(response.status, 404)
        self.assertEqual(response.payload, {"error": "not_found"})

    def test_cover_endpoint_serves_cover_by_uuid(self) -> None:
        with TemporaryDirectory() as directory:
            fixture = create_calibre_fixture_library(Path(directory) / "library")
            settings = _settings(Path(directory), library_path=fixture.root)
            initialize_companion_db(settings.companion_db_path)
            device_token = create_device_token(settings.companion_db_path, "Clara")

            response = handle_get(
                (
                    f"/kobo/{device_token.token}/{fixture.books[0].uuid}"
                    "/300/400/false/image.jpg"
                ),
                settings,
            )
            self.assertIsNotNone(response.file_path)
            response_body = response.file_path.read_bytes()

        self.assertEqual(response.status, 200)
        self.assertEqual(response.content_type, "image/jpeg")
        self.assertEqual(response_body, SMALL_JPEG)

    def test_cover_endpoint_accepts_quality_and_cache_busting_suffix(self) -> None:
        with TemporaryDirectory() as directory:
            fixture = create_calibre_fixture_library(Path(directory) / "library")
            settings = _settings(Path(directory), library_path=fixture.root)
            initialize_companion_db(settings.companion_db_path)
            device_token = create_device_token(settings.companion_db_path, "Clara")

            response = handle_get(
                (
                    f"/kobo/{device_token.token}/{fixture.books[0].uuid}-20240201"
                    "/300/400/90/false/image.jpg"
                ),
                settings,
            )
            self.assertIsNotNone(response.file_path)
            response_body = response.file_path.read_bytes()

        self.assertEqual(response.status, 200)
        self.assertEqual(response.content_type, "image/jpeg")
        self.assertEqual(response_body, SMALL_JPEG)

    def test_read_only_requests_do_not_modify_calibre_metadata_db(self) -> None:
        with TemporaryDirectory() as directory:
            fixture = create_calibre_fixture_library(Path(directory) / "library")
            settings = _settings(Path(directory), library_path=fixture.root)
            initialize_companion_db(settings.companion_db_path)
            device_token = create_device_token(settings.companion_db_path, "Clara")
            before_mtime = fixture.metadata_db_path.stat().st_mtime_ns

            responses = [
                handle_get(f"/kobo/{device_token.token}/v1/library/sync", settings),
                handle_get(
                    (
                        f"/kobo/{device_token.token}/v1/library/"
                        f"{fixture.books[0].uuid}/metadata"
                    ),
                    settings,
                ),
                handle_get(
                    f"/kobo/{device_token.token}/download/{fixture.books[0].id}/epub",
                    settings,
                ),
                handle_get(
                    (
                        f"/kobo/{device_token.token}/{fixture.books[0].uuid}"
                        "/300/400/false/image.jpg"
                    ),
                    settings,
                ),
            ]
            after_mtime = fixture.metadata_db_path.stat().st_mtime_ns

        self.assertEqual([response.status for response in responses], [200, 200, 200, 200])
        self.assertEqual(after_mtime, before_mtime)


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
