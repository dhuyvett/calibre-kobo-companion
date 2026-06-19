from __future__ import annotations

import base64
import json
from pathlib import Path
from tempfile import TemporaryDirectory
from unittest import TestCase
from unittest.mock import Mock, patch

from calibre_fixture import SMALL_JPEG, create_calibre_fixture_library
from calibre_kobo_companion.config import ConfigError, Settings
from calibre_kobo_companion.db import (
    create_device_token,
    initialize_companion_db,
    revoke_device_token,
)
from calibre_kobo_companion.kobo import SyncToken, encode_sync_token
from calibre_kobo_companion.server import (
    _tls_context,
    create_server,
    handle_delete,
    handle_get,
    handle_post,
    handle_put,
)


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

    def test_create_server_wraps_socket_when_tls_is_enabled(self) -> None:
        with TemporaryDirectory() as directory:
            root = Path(directory)
            cert_path = root / "fullchain.pem"
            key_path = root / "privkey.pem"
            cert_path.write_text("certificate", encoding="utf-8")
            key_path.write_text("key", encoding="utf-8")
            settings = _settings(
                root,
                listen_host="127.0.0.1",
                listen_port=0,
                tls_cert_path=cert_path,
                tls_key_path=key_path,
            )
            wrapped_socket = Mock()
            context = Mock()
            context.wrap_socket.return_value = wrapped_socket
            fake_server = Mock()
            fake_server.socket = Mock()

            with patch("calibre_kobo_companion.server.CompanionServer") as server_class:
                server_class.return_value = fake_server
                with patch(
                    "calibre_kobo_companion.server._tls_context",
                    return_value=context,
                ) as tls_context:
                    server = create_server(settings)

            try:
                self.assertIs(server, fake_server)
                self.assertIs(server.socket, wrapped_socket)
                server_class.assert_called_once_with(("127.0.0.1", 0), settings)
                tls_context.assert_called_once_with(settings)
                context.wrap_socket.assert_called_once()
                self.assertTrue(context.wrap_socket.call_args.kwargs["server_side"])
            finally:
                server.server_close()

    def test_create_server_rejects_missing_tls_files(self) -> None:
        with TemporaryDirectory() as directory:
            root = Path(directory)
            settings = _settings(
                root,
                listen_host="127.0.0.1",
                listen_port=0,
                tls_cert_path=root / "missing-fullchain.pem",
                tls_key_path=root / "missing-privkey.pem",
            )

            with self.assertRaisesRegex(ConfigError, "TLS_CERT_PATH"):
                _tls_context(settings)

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
        self.assertEqual(response.headers["x-kobo-apitoken"], "e30=")
        resources = response.payload["Resources"]
        self.assertEqual(
            resources["LibrarySync"],
            f"http://example.test/kobo/{device_token.token}/v1/library/sync",
        )
        self.assertEqual(
            resources["AuthDevice"],
            "https://storeapi.kobo.com/v1/auth/device",
        )
        self.assertEqual(
            resources["library_sync"],
            f"http://example.test/kobo/{device_token.token}/v1/library/sync",
        )
        self.assertEqual(
            resources["device_auth"],
            "https://storeapi.kobo.com/v1/auth/device",
        )
        self.assertEqual(
            resources["user_profile"],
            "https://storeapi.kobo.com/v1/user/profile",
        )
        self.assertEqual(
            resources["image_url_template"],
            (
                f"http://example.test/kobo/{device_token.token}"
                "/{ImageId}/{Width}/{Height}/false/image.jpg"
            ),
        )
        self.assertEqual(
            resources["reading_state"],
            f"http://example.test/kobo/{device_token.token}/v1/library/{{Ids}}/state",
        )

    def test_auth_device_and_refresh_return_kobo_compatible_tokens(self) -> None:
        with TemporaryDirectory() as directory:
            settings = _settings(Path(directory))
            initialize_companion_db(settings.companion_db_path)
            device_token = create_device_token(settings.companion_db_path, "Clara")

            device_response = handle_post(
                f"/kobo/{device_token.token}/v1/auth/device",
                settings,
                {"UserKey": "device-user-key"},
            )
            refresh_response = handle_post(
                f"/kobo/{device_token.token}/v1/auth/refresh",
                settings,
            )

        self.assertEqual(device_response.status, 200)
        self.assertEqual(refresh_response.status, 200)
        self.assertEqual(device_response.payload["TokenType"], "Bearer")
        self.assertEqual(device_response.payload["UserKey"], "device-user-key")
        self.assertIn("TrackingId", device_response.payload)
        self.assertIn("RefreshToken", device_response.payload)
        self.assertNotEqual(
            device_response.payload["AccessToken"],
            refresh_response.payload["AccessToken"],
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
            user_reviews_response = handle_get(
                (
                    f"/kobo/{device_token.token}/v1/user/reviews"
                    "?ProductIds=eec4100f-96a8-4245-be72-10e82fc65111"
                ),
                settings,
            )

        self.assertEqual(profile_response.status, 200)
        self.assertEqual(assets_response.status, 200)
        self.assertEqual(analytics_response.status, 200)
        self.assertEqual(affiliate_response.status, 200)
        self.assertEqual(deals_response.status, 200)
        self.assertEqual(featured_products_response.status, 200)
        self.assertEqual(user_reviews_response.status, 200)
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
            reading_state_response = handle_put(
                (
                    f"/kobo/{device_token.token}/v1/library/"
                    "d65b0a72-704c-4822-9605-ad837e66fc17/state"
                ),
                settings,
            )

        self.assertEqual(delete_book_response.status, 200)
        self.assertEqual(delete_book_response.payload, {})
        self.assertEqual(tag_delete_response.status, 200)
        self.assertEqual(tag_delete_response.payload, {})
        self.assertEqual(reading_state_response.status, 200)
        self.assertEqual(reading_state_response.payload, {})

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

    def test_calibre_library_unavailable_returns_service_unavailable(self) -> None:
        with TemporaryDirectory() as directory:
            root = Path(directory)
            settings = _settings(root, library_path=root / "missing-library")
            initialize_companion_db(settings.companion_db_path)
            device_token = create_device_token(settings.companion_db_path, "Clara")

            with self.assertLogs("calibre_kobo_companion.server", level="WARNING") as logs:
                responses = [
                    handle_get(f"/kobo/{device_token.token}/v1/library/sync", settings),
                    handle_get(
                        (
                            f"/kobo/{device_token.token}/v1/library/"
                            "11111111-1111-4111-8111-111111111111/metadata"
                        ),
                        settings,
                    ),
                    handle_get(f"/kobo/{device_token.token}/download/1/epub", settings),
                    handle_get(
                        (
                            f"/kobo/{device_token.token}/"
                            "11111111-1111-4111-8111-111111111111/300/400/false/image.jpg"
                        ),
                        settings,
                    ),
                ]

        self.assertEqual([response.status for response in responses], [503] * 4)
        self.assertEqual(
            [response.payload for response in responses],
            [{"error": "calibre_library_unavailable"}] * 4,
        )
        self.assertEqual(len(logs.output), 4)
        self.assertIn("Calibre library unavailable during library sync", logs.output[0])

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

    def test_book_metadata_advertises_kepub_for_epub_only_book_when_conversion_enabled(
        self,
    ) -> None:
        with TemporaryDirectory() as directory:
            fixture = create_calibre_fixture_library(Path(directory) / "library")
            kepubify_path = _write_fake_kepubify(Path(directory))
            settings = _settings(
                Path(directory),
                library_path=fixture.root,
                enable_kepubify=True,
                kepubify_path=kepubify_path,
            )
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
        self.assertEqual(
            [download["Format"] for download in metadata["DownloadUrls"]],
            ["KEPUB"],
        )
        self.assertEqual(
            metadata["DownloadUrls"][0]["Url"],
            (
                f"http://example.test/kobo/{device_token.token}"
                f"/download/{fixture.books[1].id}/kepub"
            ),
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

    def test_download_returns_not_found_for_stale_format_metadata(self) -> None:
        with TemporaryDirectory() as directory:
            fixture = create_calibre_fixture_library(Path(directory) / "library")
            stale_epub_path = (
                fixture.root
                / fixture.books[1].relative_path
                / "Epub Only - Grace Hopper.epub"
            )
            stale_epub_path.unlink()
            settings = _settings(Path(directory), library_path=fixture.root)
            initialize_companion_db(settings.companion_db_path)
            device_token = create_device_token(settings.companion_db_path, "Clara")

            response = handle_get(
                f"/kobo/{device_token.token}/download/{fixture.books[1].id}/epub",
                settings,
            )

        self.assertEqual(response.status, 404)
        self.assertEqual(response.payload, {"error": "not_found"})

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

    def test_download_falls_back_to_epub_when_kepub_conversion_is_disabled(self) -> None:
        with TemporaryDirectory() as directory:
            fixture = create_calibre_fixture_library(Path(directory) / "library")
            settings = _settings(Path(directory), library_path=fixture.root)
            initialize_companion_db(settings.companion_db_path)
            device_token = create_device_token(settings.companion_db_path, "Clara")

            response = handle_get(
                f"/kobo/{device_token.token}/download/{fixture.books[1].id}/kepub",
                settings,
            )
            self.assertIsNotNone(response.file_path)
            response_body = response.file_path.read_bytes()

        self.assertEqual(response.status, 200)
        self.assertEqual(response.content_type, "application/epub+zip")
        self.assertEqual(response_body, b"Epub Only fixture EPUB\n")

    def test_download_converts_epub_only_book_to_kepub_and_uses_cache(self) -> None:
        with TemporaryDirectory() as directory:
            root = Path(directory)
            fixture = create_calibre_fixture_library(root / "library")
            kepubify_path = _write_fake_kepubify(root)
            settings = _settings(
                root,
                library_path=fixture.root,
                enable_kepubify=True,
                kepubify_path=kepubify_path,
            )
            initialize_companion_db(settings.companion_db_path)
            device_token = create_device_token(settings.companion_db_path, "Clara")

            first_response = handle_get(
                f"/kobo/{device_token.token}/download/{fixture.books[1].id}/kepub",
                settings,
            )
            self.assertIsNotNone(first_response.file_path)
            first_path = first_response.file_path
            first_body = first_path.read_bytes()

            second_response = handle_get(
                f"/kobo/{device_token.token}/download/{fixture.books[1].id}/kepub",
                settings,
            )
            self.assertIsNotNone(second_response.file_path)
            second_path = second_response.file_path
            second_body = second_path.read_bytes()
            kepubify_runs = (root / "kepubify-runs.txt").read_text(encoding="utf-8")

        self.assertEqual(first_response.status, 200)
        self.assertEqual(first_response.content_type, "application/vnd.kobo.kepub+zip")
        self.assertEqual(first_body, b"converted:Epub Only fixture EPUB\n")
        self.assertEqual(second_response.status, 200)
        self.assertEqual(second_body, first_body)
        self.assertEqual(second_path, first_path)
        self.assertEqual(kepubify_runs.count("run\n"), 1)

    def test_kepub_conversion_does_not_modify_calibre_library_files(self) -> None:
        with TemporaryDirectory() as directory:
            root = Path(directory)
            fixture = create_calibre_fixture_library(root / "library")
            epub_path = (
                fixture.root
                / fixture.books[1].relative_path
                / "Epub Only - Grace Hopper.epub"
            )
            before_db_mtime = fixture.metadata_db_path.stat().st_mtime_ns
            before_epub_mtime = epub_path.stat().st_mtime_ns
            kepubify_path = _write_fake_kepubify(root)
            settings = _settings(
                root,
                library_path=fixture.root,
                enable_kepubify=True,
                kepubify_path=kepubify_path,
            )
            initialize_companion_db(settings.companion_db_path)
            device_token = create_device_token(settings.companion_db_path, "Clara")

            response = handle_get(
                f"/kobo/{device_token.token}/download/{fixture.books[1].id}/kepub",
                settings,
            )
            after_db_mtime = fixture.metadata_db_path.stat().st_mtime_ns
            after_epub_mtime = epub_path.stat().st_mtime_ns

        self.assertEqual(response.status, 200)
        self.assertEqual(after_db_mtime, before_db_mtime)
        self.assertEqual(after_epub_mtime, before_epub_mtime)

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

    def test_cover_endpoint_returns_not_found_for_stale_cover_metadata(self) -> None:
        with TemporaryDirectory() as directory:
            fixture = create_calibre_fixture_library(Path(directory) / "library")
            stale_cover_path = fixture.root / fixture.books[0].relative_path / "cover.jpg"
            stale_cover_path.unlink()
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

        self.assertEqual(response.status, 404)
        self.assertEqual(response.payload, {"error": "not_found"})

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
    listen_host: str = "0.0.0.0",
    listen_port: int = 8080,
    kobo_sync_page_size: int = 100,
    enable_kepubify: bool = False,
    kepubify_path: Path | None = None,
    tls_cert_path: Path | None = None,
    tls_key_path: Path | None = None,
) -> Settings:
    return Settings(
        calibre_library_path=library_path or directory / "library",
        companion_db_path=directory / "companion.db",
        companion_cache_path=directory / "cache",
        public_base_url="http://example.test",
        listen_host=listen_host,
        listen_port=listen_port,
        kobo_sync_page_size=kobo_sync_page_size,
        enable_kepubify=enable_kepubify,
        kepubify_path=kepubify_path,
        tls_cert_path=tls_cert_path,
        tls_key_path=tls_key_path,
    )


def _write_fake_kepubify(directory: Path) -> Path:
    script_path = directory / "fake-kepubify.py"
    log_path = directory / "kepubify-runs.txt"
    script_path.write_text(
        "\n".join(
            [
                "#!/usr/bin/env python3",
                "from pathlib import Path",
                "import sys",
                "",
                "output_dir = Path(sys.argv[sys.argv.index('-o') + 1])",
                "source_path = Path(sys.argv[-1])",
                f"log_path = Path({str(log_path)!r})",
                "log_path.write_text(log_path.read_text(encoding='utf-8') + 'run\\n' if log_path.exists() else 'run\\n', encoding='utf-8')",
                "output_dir.mkdir(parents=True, exist_ok=True)",
                "output_path = output_dir / f'{source_path.stem}.kepub.epub'",
                "output_path.write_bytes(b'converted:' + source_path.read_bytes())",
            ]
        )
        + "\n",
        encoding="utf-8",
    )
    script_path.chmod(0o755)
    return script_path
