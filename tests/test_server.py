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
from calibre_kobo_companion.kobo import (
    HybridSyncToken,
    SyncToken,
    decode_hybrid_sync_token,
    encode_hybrid_sync_token,
    encode_sync_token,
)
from calibre_kobo_companion.kobo_proxy import (
    KoboBinaryProxyResponse,
    KoboProxyResponse,
    KoboStoreUnavailable,
)
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

    def test_hybrid_initialization_preserves_official_api_token(self) -> None:
        with TemporaryDirectory() as directory:
            settings = _settings(Path(directory), kobo_sync_mode="hybrid")
            initialize_companion_db(settings.companion_db_path)
            device_token = create_device_token(settings.companion_db_path, "Clara")

            with patch(
                "calibre_kobo_companion.server.proxy_kobo_get",
                return_value=KoboProxyResponse(
                    status=200,
                    payload={
                        "Resources": {
                            "LibrarySync": "https://storeapi.kobo.com/v1/library/sync",
                            "user_profile": "https://storeapi.kobo.com/v1/user/profile",
                        },
                        "ReleaseNoteURL": "https://example.test/release",
                    },
                    headers={"x-kobo-apitoken": "official-api-token"},
                ),
            ) as proxy:
                response = handle_get(
                    f"/kobo/{device_token.token}/v1/initialization",
                    settings,
                    {"User-Agent": "Kobo", "x-kobo-apitoken": "e30="},
                )

        self.assertEqual(response.status, 200)
        self.assertEqual(response.headers["x-kobo-apitoken"], "official-api-token")
        self.assertEqual(response.payload["ReleaseNoteURL"], "https://example.test/release")
        resources = response.payload["Resources"]
        self.assertEqual(
            resources["LibrarySync"],
            f"http://example.test/kobo/{device_token.token}/v1/library/sync",
        )
        self.assertEqual(
            resources["BookMetadata"],
            f"http://example.test/kobo/{device_token.token}/v1/library/{{RevisionId}}/metadata",
        )
        self.assertEqual(
            resources["user_profile"],
            "https://storeapi.kobo.com/v1/user/profile",
        )
        proxy.assert_called_once()
        self.assertEqual(proxy.call_args.args[0], "/v1/initialization")
        self.assertEqual(proxy.call_args.args[2], {"User-Agent": "Kobo"})

    def test_hybrid_initialization_falls_back_when_kobo_rejects_init(self) -> None:
        with TemporaryDirectory() as directory:
            settings = _settings(Path(directory), kobo_sync_mode="hybrid")
            initialize_companion_db(settings.companion_db_path)
            device_token = create_device_token(settings.companion_db_path, "Clara")

            with patch(
                "calibre_kobo_companion.server.proxy_kobo_get",
                return_value=KoboProxyResponse(
                    status=400,
                    payload={"error": "bad_request"},
                    headers={},
                ),
            ):
                with self.assertLogs("calibre_kobo_companion.server", level="WARNING"):
                    response = handle_get(
                        f"/kobo/{device_token.token}/v1/initialization",
                        settings,
                    )

        self.assertEqual(response.status, 200)
        self.assertNotIn("x-kobo-apitoken", response.headers)
        self.assertEqual(
            response.payload["Resources"]["LibrarySync"],
            f"http://example.test/kobo/{device_token.token}/v1/library/sync",
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

    def test_hybrid_auth_refresh_is_proxied_to_kobo(self) -> None:
        with TemporaryDirectory() as directory:
            settings = _settings(Path(directory), kobo_sync_mode="hybrid")
            initialize_companion_db(settings.companion_db_path)
            device_token = create_device_token(settings.companion_db_path, "Clara")
            payload = {"RefreshToken": "device-refresh-token"}

            with patch(
                "calibre_kobo_companion.server.proxy_kobo_request",
                return_value=KoboProxyResponse(
                    status=200,
                    payload={
                        "AccessToken": "official-access-token",
                        "RefreshToken": "official-refresh-token",
                        "TokenType": "Bearer",
                    },
                    headers={},
                ),
            ) as proxy:
                response = handle_post(
                    f"/kobo/{device_token.token}/v1/auth/refresh",
                    settings,
                    payload,
                    {
                        "Authorization": "Bearer stale-token",
                        "x-kobo-apitoken": "e30=",
                    },
                )

        self.assertEqual(response.status, 200)
        self.assertEqual(response.payload["AccessToken"], "official-access-token")
        proxy.assert_called_once()
        self.assertEqual(proxy.call_args.args[0], "POST")
        self.assertEqual(proxy.call_args.args[1], "/v1/auth/refresh")
        self.assertEqual(proxy.call_args.args[3], {"Authorization": "Bearer stale-token"})
        self.assertEqual(proxy.call_args.kwargs["payload"], payload)

    def test_hybrid_sync_token_round_trips_local_and_kobo_state(self) -> None:
        encoded = encode_hybrid_sync_token(
            HybridSyncToken(
                kobo="official-token",
                local=SyncToken(since="2024-02-01 12:00:00+00:00", offset=3),
            )
        )

        decoded = decode_hybrid_sync_token(encoded)

        self.assertEqual(decoded.kobo, "official-token")
        self.assertEqual(decoded.local.since, "2024-02-01 12:00:00+00:00")
        self.assertEqual(decoded.local.offset, 3)

    def test_hybrid_sync_token_treats_raw_kobo_token_as_official_state(self) -> None:
        decoded = decode_hybrid_sync_token("raw-kobo-token")

        self.assertEqual(decoded.kobo, "raw-kobo-token")
        self.assertIsNone(decoded.local.since)
        self.assertEqual(decoded.local.offset, 0)

    def test_hybrid_sync_token_migrates_legacy_local_sync_token(self) -> None:
        legacy_token = encode_sync_token(
            SyncToken(since="2024-02-02 12:00:00+00:00", offset=2)
        )

        decoded = decode_hybrid_sync_token(legacy_token)

        self.assertIsNone(decoded.kobo)
        self.assertEqual(decoded.local.since, "2024-02-02 12:00:00+00:00")
        self.assertEqual(decoded.local.offset, 2)

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

    def test_hybrid_user_profile_is_proxied_to_kobo(self) -> None:
        with TemporaryDirectory() as directory:
            settings = _settings(Path(directory), kobo_sync_mode="hybrid")
            initialize_companion_db(settings.companion_db_path)
            device_token = create_device_token(settings.companion_db_path, "Clara")

            with patch(
                "calibre_kobo_companion.server.proxy_kobo_get",
                return_value=KoboProxyResponse(
                    status=401,
                    payload={"error": "session_expired"},
                    headers={},
                ),
            ) as proxy:
                with self.assertLogs("calibre_kobo_companion.server", level="WARNING"):
                    response = handle_get(
                        f"/kobo/{device_token.token}/v1/user/profile",
                        settings,
                        {"Authorization": "Bearer stale-token"},
                    )

        self.assertEqual(response.status, 401)
        self.assertEqual(response.payload, {"error": "session_expired"})
        proxy.assert_called_once()
        self.assertEqual(proxy.call_args.args[0], "/v1/user/profile")

    def test_hybrid_user_profile_forbidden_prompts_auth_refresh(self) -> None:
        with TemporaryDirectory() as directory:
            settings = _settings(Path(directory), kobo_sync_mode="hybrid")
            initialize_companion_db(settings.companion_db_path)
            device_token = create_device_token(settings.companion_db_path, "Clara")

            with patch(
                "calibre_kobo_companion.server.proxy_kobo_get",
                return_value=KoboProxyResponse(
                    status=403,
                    payload={"error": "forbidden"},
                    headers={},
                ),
            ):
                with self.assertLogs("calibre_kobo_companion.server", level="WARNING"):
                    response = handle_get(
                        f"/kobo/{device_token.token}/v1/user/profile",
                        settings,
                        {
                            "Authorization": "Bearer stale-token",
                            "x-kobo-apitoken": "e30=",
                        },
                    )

        self.assertEqual(response.status, 401)
        self.assertEqual(response.payload, {"error": "forbidden"})

    def test_hybrid_user_profile_preserves_kobo_api_token_header(self) -> None:
        with TemporaryDirectory() as directory:
            settings = _settings(Path(directory), kobo_sync_mode="hybrid")
            initialize_companion_db(settings.companion_db_path)
            device_token = create_device_token(settings.companion_db_path, "Clara")

            with patch(
                "calibre_kobo_companion.server.proxy_kobo_get",
                return_value=KoboProxyResponse(
                    status=200,
                    payload={"UserDisplayName": "Kobo User"},
                    headers={"x-kobo-apitoken": "official-api-token"},
                ),
            ):
                response = handle_get(
                    f"/kobo/{device_token.token}/v1/user/profile",
                    settings,
                    {"Authorization": "Bearer official-token"},
                )

        self.assertEqual(response.status, 200)
        self.assertEqual(response.payload, {"UserDisplayName": "Kobo User"})
        self.assertEqual(response.headers["x-kobo-apitoken"], "official-api-token")

    def test_hybrid_native_get_endpoints_are_proxied_to_kobo(self) -> None:
        with TemporaryDirectory() as directory:
            settings = _settings(Path(directory), kobo_sync_mode="hybrid")
            initialize_companion_db(settings.companion_db_path)
            device_token = create_device_token(settings.companion_db_path, "Clara")

            with patch(
                "calibre_kobo_companion.server.proxy_kobo_get",
                return_value=KoboProxyResponse(
                    status=200,
                    payload={"Items": []},
                    headers={"etag": "wishlist-etag"},
                ),
            ) as proxy:
                wishlist_response = handle_get(
                    (
                        f"/kobo/{device_token.token}/v1/user/wishlist"
                        "?PageSize=100&PageIndex=0"
                    ),
                    settings,
                    {
                        "Authorization": "Bearer official-token",
                        "If-None-Match": "old-etag",
                        "X-Kobo-AppVersion": "4.38.23697",
                        "X-Kobo-PlatformId": "00000000-0000-0000-0000-000000000388",
                    },
                )
                product_response = handle_get(
                    (
                        f"/kobo/{device_token.token}/v1/products/"
                        "1f06bb2a-c8ab-4859-b48f-e4f24e8259d3/nextread"
                    ),
                    settings,
                    {"Authorization": "Bearer official-token"},
                )

        self.assertEqual(wishlist_response.status, 200)
        self.assertEqual(wishlist_response.headers["etag"], "wishlist-etag")
        self.assertEqual(product_response.status, 200)
        self.assertEqual(proxy.call_count, 2)
        self.assertEqual(proxy.call_args_list[0].args[0], "/v1/user/wishlist")
        self.assertEqual(proxy.call_args_list[0].args[1], "PageSize=100&PageIndex=0")
        self.assertEqual(proxy.call_args_list[0].args[2]["If-None-Match"], "old-etag")
        self.assertEqual(proxy.call_args_list[0].args[2]["X-Kobo-AppVersion"], "4.38.23697")
        self.assertEqual(
            proxy.call_args_list[0].args[2]["X-Kobo-PlatformId"],
            "00000000-0000-0000-0000-000000000388",
        )
        self.assertEqual(
            proxy.call_args_list[1].args[0],
            "/v1/products/1f06bb2a-c8ab-4859-b48f-e4f24e8259d3/nextread",
        )

    def test_hybrid_unknown_get_is_proxied_to_kobo(self) -> None:
        with TemporaryDirectory() as directory:
            settings = _settings(Path(directory), kobo_sync_mode="hybrid")
            initialize_companion_db(settings.companion_db_path)
            device_token = create_device_token(settings.companion_db_path, "Clara")

            with patch(
                "calibre_kobo_companion.server.proxy_kobo_get",
                return_value=KoboProxyResponse(
                    status=200,
                    payload={"Result": "Success"},
                    headers={
                        "Content-Encoding": "gzip",
                        "X-New-Kobo-Response": "new-value",
                    },
                ),
            ) as proxy:
                response = handle_get(
                    (
                        f"/kobo/{device_token.token}/v1/new/native/endpoint"
                        "?IncludePreview=true"
                    ),
                    settings,
                    {
                        "Authorization": "Bearer official-token",
                        "X-New-Kobo-Header": "new-value",
                    },
                )

        self.assertEqual(response.status, 200)
        self.assertEqual(response.payload, {"Result": "Success"})
        self.assertNotIn("Content-Encoding", response.headers)
        self.assertEqual(response.headers["X-New-Kobo-Response"], "new-value")
        proxy.assert_called_once()
        self.assertEqual(proxy.call_args.args[0], "/v1/new/native/endpoint")
        self.assertEqual(proxy.call_args.args[1], "IncludePreview=true")
        self.assertEqual(proxy.call_args.args[2]["Authorization"], "Bearer official-token")
        self.assertEqual(proxy.call_args.args[2]["X-New-Kobo-Header"], "new-value")

    def test_hybrid_unknown_post_is_proxied_to_kobo(self) -> None:
        with TemporaryDirectory() as directory:
            settings = _settings(Path(directory), kobo_sync_mode="hybrid")
            initialize_companion_db(settings.companion_db_path)
            device_token = create_device_token(settings.companion_db_path, "Clara")
            payload = {"Event": "OpenedStore"}
            body = b'{"Event" : "OpenedStore"}'

            with patch(
                "calibre_kobo_companion.server.proxy_kobo_request",
                return_value=KoboProxyResponse(
                    status=202,
                    payload={"Accepted": True},
                    headers={},
                ),
            ) as proxy:
                response = handle_post(
                    (
                        f"/kobo/{device_token.token}/v1/new/native/action"
                        "?source=device"
                    ),
                    settings,
                    payload,
                    {
                        "Authorization": "Bearer official-token",
                        "Content-Type": "application/json",
                    },
                    body,
                )

        self.assertEqual(response.status, 202)
        self.assertEqual(response.payload, {"Accepted": True})
        proxy.assert_called_once()
        self.assertEqual(proxy.call_args.args[0], "POST")
        self.assertEqual(proxy.call_args.args[1], "/v1/new/native/action")
        self.assertEqual(proxy.call_args.args[2], "source=device")
        self.assertEqual(proxy.call_args.args[3]["Authorization"], "Bearer official-token")
        self.assertEqual(proxy.call_args.args[3]["Content-Type"], "application/json")
        self.assertEqual(proxy.call_args.kwargs["payload"], payload)
        self.assertEqual(proxy.call_args.kwargs["body"], body)

    def test_hybrid_overdrive_borrow_is_proxied_to_kobo(self) -> None:
        with TemporaryDirectory() as directory:
            settings = _settings(Path(directory), kobo_sync_mode="hybrid")
            initialize_companion_db(settings.companion_db_path)
            device_token = create_device_token(settings.companion_db_path, "Clara")

            with patch(
                "calibre_kobo_companion.server.proxy_kobo_get",
                return_value=KoboProxyResponse(
                    status=200,
                    payload={"EntitlementId": "overdrive-entitlement"},
                    headers={},
                ),
            ) as proxy:
                response = handle_get(
                    (
                        f"/kobo/{device_token.token}/v1/library/borrow"
                        "?origin=kobo&KoboTitleId=od_9724722&ExpiryDate=1783880500"
                    ),
                    settings,
                    {
                        "Authorization": "Bearer official-token",
                        "X-Kobo-AppVersion": "4.38.23697",
                    },
                )

        self.assertEqual(response.status, 200)
        self.assertEqual(response.payload, {"EntitlementId": "overdrive-entitlement"})
        proxy.assert_called_once()
        self.assertEqual(proxy.call_args.args[0], "/v1/library/borrow")
        self.assertEqual(
            proxy.call_args.args[1],
            "origin=kobo&KoboTitleId=od_9724722&ExpiryDate=1783880500",
        )
        self.assertEqual(proxy.call_args.args[2]["Authorization"], "Bearer official-token")
        self.assertEqual(proxy.call_args.args[2]["X-Kobo-AppVersion"], "4.38.23697")

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

    def test_hybrid_local_reading_state_is_acknowledged_without_proxying(self) -> None:
        with TemporaryDirectory() as directory:
            fixture = create_calibre_fixture_library(Path(directory) / "library")
            settings = _settings(
                Path(directory),
                library_path=fixture.root,
                kobo_sync_mode="hybrid",
            )
            initialize_companion_db(settings.companion_db_path)
            device_token = create_device_token(settings.companion_db_path, "Clara")

            with patch("calibre_kobo_companion.server.proxy_kobo_request") as proxy:
                response = handle_put(
                    (
                        f"/kobo/{device_token.token}/v1/library/"
                        f"{fixture.books[0].uuid}/state"
                    ),
                    settings,
                    {"ReadingState": {"Status": "Finished"}},
                )

        self.assertEqual(response.status, 200)
        self.assertEqual(response.payload, {})
        proxy.assert_not_called()

    def test_hybrid_official_reading_state_is_proxied(self) -> None:
        with TemporaryDirectory() as directory:
            fixture = create_calibre_fixture_library(Path(directory) / "library")
            settings = _settings(
                Path(directory),
                library_path=fixture.root,
                kobo_sync_mode="hybrid",
            )
            initialize_companion_db(settings.companion_db_path)
            device_token = create_device_token(settings.companion_db_path, "Clara")
            payload = {"ReadingState": {"Status": "Finished"}}

            with patch(
                "calibre_kobo_companion.server.proxy_kobo_request",
                return_value=KoboProxyResponse(status=204, payload={}, headers={}),
            ) as proxy:
                response = handle_put(
                    (
                        f"/kobo/{device_token.token}/v1/library/"
                        "official-book/state?merge=true"
                    ),
                    settings,
                    payload,
                    {"Authorization": "Bearer secret"},
                )

        self.assertEqual(response.status, 204)
        proxy.assert_called_once()
        self.assertEqual(proxy.call_args.args[0], "PUT")
        self.assertEqual(proxy.call_args.args[1], "/v1/library/official-book/state")
        self.assertEqual(proxy.call_args.args[2], "merge=true")
        self.assertEqual(proxy.call_args.kwargs["payload"], payload)

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

    def test_hybrid_library_sync_merges_official_and_local_entitlements(self) -> None:
        with TemporaryDirectory() as directory:
            fixture = create_calibre_fixture_library(Path(directory) / "library")
            settings = _settings(
                Path(directory),
                library_path=fixture.root,
                kobo_sync_mode="hybrid",
            )
            initialize_companion_db(settings.companion_db_path)
            device_token = create_device_token(settings.companion_db_path, "Clara")
            official_payload = [
                {
                    "NewEntitlement": {
                        "BookEntitlement": {"Id": "official-book"},
                        "BookMetadata": {"Title": "Official Book"},
                    }
                }
            ]

            with patch(
                "calibre_kobo_companion.server.proxy_kobo_get",
                return_value=KoboProxyResponse(
                    status=200,
                    payload=official_payload,
                    headers={"x-kobo-synctoken": "official-next-token"},
                ),
            ) as proxy:
                response = handle_get(
                    (
                        f"/kobo/{device_token.token}/v1/library/sync"
                        "?Filter=ALL&DownloadUrlFilter=Generic"
                    ),
                    settings,
                    {
                        "Authorization": "Bearer secret",
                        "x-kobo-apitoken": "e30=",
                        "x-kobo-synctoken": "official-current-token",
                    },
                )

        self.assertEqual(response.status, 200)
        self.assertEqual(len(response.payload), 3)
        self.assertEqual(
            response.payload[0]["NewEntitlement"]["BookMetadata"]["Title"],
            "Official Book",
        )
        self.assertEqual(
            response.payload[1]["NewEntitlement"]["BookMetadata"]["Title"],
            "Existing Kepub",
        )
        returned_token = decode_hybrid_sync_token(response.headers["x-kobo-synctoken"])
        self.assertEqual(returned_token.kobo, "official-next-token")
        self.assertEqual(returned_token.local.since, "2024-02-02 12:00:00+00:00")
        proxy.assert_called_once()
        self.assertEqual(proxy.call_args.args[0], "/v1/library/sync")
        self.assertEqual(proxy.call_args.args[1], "Filter=ALL&DownloadUrlFilter=Generic")
        self.assertEqual(proxy.call_args.args[2]["Authorization"], "Bearer secret")
        self.assertNotIn("x-kobo-apitoken", proxy.call_args.args[2])
        self.assertEqual(proxy.call_args.kwargs["sync_token"], "official-current-token")

    def test_hybrid_library_sync_falls_back_to_local_when_kobo_forbidden(self) -> None:
        with TemporaryDirectory() as directory:
            fixture = create_calibre_fixture_library(Path(directory) / "library")
            settings = _settings(
                Path(directory),
                library_path=fixture.root,
                kobo_sync_mode="hybrid",
            )
            initialize_companion_db(settings.companion_db_path)
            device_token = create_device_token(settings.companion_db_path, "Clara")

            with patch(
                "calibre_kobo_companion.server.proxy_kobo_get",
                return_value=KoboProxyResponse(
                    status=403,
                    payload={"error": "forbidden"},
                    headers={},
                ),
            ):
                with self.assertLogs(
                    "calibre_kobo_companion.server",
                    level="WARNING",
                ) as logs:
                    response = handle_get(
                        f"/kobo/{device_token.token}/v1/library/sync",
                        settings,
                        {"Authorization": "Bearer stale-token"},
                    )

        self.assertEqual(response.status, 200)
        self.assertEqual(len(response.payload), 2)
        self.assertIn("'authorization': True", logs.output[0])

    def test_hybrid_library_sync_does_not_forward_legacy_local_sync_token(self) -> None:
        with TemporaryDirectory() as directory:
            fixture = create_calibre_fixture_library(Path(directory) / "library")
            settings = _settings(
                Path(directory),
                library_path=fixture.root,
                kobo_sync_mode="hybrid",
            )
            initialize_companion_db(settings.companion_db_path)
            device_token = create_device_token(settings.companion_db_path, "Clara")
            legacy_token = encode_sync_token(
                SyncToken(since="2024-02-02 12:00:00+00:00", offset=0)
            )

            with patch(
                "calibre_kobo_companion.server.proxy_kobo_get",
                return_value=KoboProxyResponse(
                    status=200,
                    payload=[],
                    headers={"x-kobo-synctoken": "official-next-token"},
                ),
            ) as proxy:
                response = handle_get(
                    f"/kobo/{device_token.token}/v1/library/sync",
                    settings,
                    {"x-kobo-synctoken": legacy_token},
                )

        self.assertEqual(response.status, 200)
        proxy.assert_called_once()
        self.assertIsNone(proxy.call_args.kwargs["sync_token"])

    def test_hybrid_library_sync_retries_without_rejected_kobo_sync_token(self) -> None:
        with TemporaryDirectory() as directory:
            fixture = create_calibre_fixture_library(Path(directory) / "library")
            settings = _settings(
                Path(directory),
                library_path=fixture.root,
                kobo_sync_mode="hybrid",
            )
            initialize_companion_db(settings.companion_db_path)
            device_token = create_device_token(settings.companion_db_path, "Clara")
            hybrid_token = encode_hybrid_sync_token(
                HybridSyncToken(kobo="stale-official-token")
            )

            with patch(
                "calibre_kobo_companion.server.proxy_kobo_get",
                side_effect=[
                    KoboProxyResponse(
                        status=400,
                        payload={"error": "bad_sync_token"},
                        headers={},
                    ),
                    KoboProxyResponse(
                        status=200,
                        payload=[],
                        headers={"x-kobo-synctoken": "official-next-token"},
                    ),
                ],
            ) as proxy:
                with self.assertLogs("calibre_kobo_companion.server", level="WARNING"):
                    response = handle_get(
                        f"/kobo/{device_token.token}/v1/library/sync",
                        settings,
                        {"x-kobo-synctoken": hybrid_token},
                    )

        self.assertEqual(response.status, 200)
        self.assertEqual(proxy.call_count, 2)
        self.assertEqual(proxy.call_args_list[0].kwargs["sync_token"], "stale-official-token")
        self.assertIsNone(proxy.call_args_list[1].kwargs.get("sync_token"))

    def test_hybrid_library_sync_falls_back_to_local_on_official_auth_failure(
        self,
    ) -> None:
        with TemporaryDirectory() as directory:
            fixture = create_calibre_fixture_library(Path(directory) / "library")
            settings = _settings(
                Path(directory),
                library_path=fixture.root,
                kobo_sync_mode="hybrid",
            )
            initialize_companion_db(settings.companion_db_path)
            device_token = create_device_token(settings.companion_db_path, "Clara")

            with patch(
                "calibre_kobo_companion.server.proxy_kobo_get",
                return_value=KoboProxyResponse(
                    status=401,
                    payload={"error": "invalid_session"},
                    headers={"x-kobo-synctoken": "official-token"},
                ),
            ):
                with self.assertLogs("calibre_kobo_companion.server", level="WARNING"):
                    response = handle_get(
                        f"/kobo/{device_token.token}/v1/library/sync",
                        settings,
                    )

        self.assertEqual(response.status, 200)
        self.assertEqual(len(response.payload), 2)
        self.assertIn("x-kobo-synctoken", response.headers)

    def test_hybrid_library_sync_returns_bad_gateway_when_kobo_store_unavailable(
        self,
    ) -> None:
        with TemporaryDirectory() as directory:
            fixture = create_calibre_fixture_library(Path(directory) / "library")
            settings = _settings(
                Path(directory),
                library_path=fixture.root,
                kobo_sync_mode="hybrid",
            )
            initialize_companion_db(settings.companion_db_path)
            device_token = create_device_token(settings.companion_db_path, "Clara")

            with patch(
                "calibre_kobo_companion.server.proxy_kobo_get",
                side_effect=KoboStoreUnavailable("timeout"),
            ):
                with self.assertLogs("calibre_kobo_companion.server", level="WARNING"):
                    response = handle_get(
                        f"/kobo/{device_token.token}/v1/library/sync",
                        settings,
                    )

        self.assertEqual(response.status, 502)
        self.assertEqual(response.payload, {"error": "kobo_store_unavailable"})

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

    def test_hybrid_book_metadata_serves_local_uuid_without_proxying(self) -> None:
        with TemporaryDirectory() as directory:
            fixture = create_calibre_fixture_library(Path(directory) / "library")
            settings = _settings(
                Path(directory),
                library_path=fixture.root,
                kobo_sync_mode="hybrid",
            )
            initialize_companion_db(settings.companion_db_path)
            device_token = create_device_token(settings.companion_db_path, "Clara")

            with patch("calibre_kobo_companion.server.proxy_kobo_get") as proxy:
                response = handle_get(
                    (
                        f"/kobo/{device_token.token}/v1/library/"
                        f"{fixture.books[1].uuid}/metadata"
                    ),
                    settings,
                )

        self.assertEqual(response.status, 200)
        self.assertEqual(response.payload[0]["Id"], fixture.books[1].uuid)
        proxy.assert_not_called()

    def test_hybrid_book_metadata_proxies_unknown_official_id(self) -> None:
        with TemporaryDirectory() as directory:
            fixture = create_calibre_fixture_library(Path(directory) / "library")
            settings = _settings(
                Path(directory),
                library_path=fixture.root,
                kobo_sync_mode="hybrid",
            )
            initialize_companion_db(settings.companion_db_path)
            device_token = create_device_token(settings.companion_db_path, "Clara")

            with patch(
                "calibre_kobo_companion.server.proxy_kobo_get",
                return_value=KoboProxyResponse(
                    status=200,
                    payload=[{"Id": "official-book", "Title": "Official Book"}],
                    headers={},
                ),
            ) as proxy:
                response = handle_get(
                    (
                        f"/kobo/{device_token.token}/v1/library/"
                        "official-book/metadata?revision=latest"
                    ),
                    settings,
                    {"Authorization": "Bearer secret"},
                )

        self.assertEqual(response.status, 200)
        self.assertEqual(response.payload, [{"Id": "official-book", "Title": "Official Book"}])
        proxy.assert_called_once()
        self.assertEqual(proxy.call_args.args[0], "/v1/library/official-book/metadata")
        self.assertEqual(proxy.call_args.args[1], "revision=latest")

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

    def test_hybrid_cover_endpoint_serves_local_cover_without_proxying(self) -> None:
        with TemporaryDirectory() as directory:
            fixture = create_calibre_fixture_library(Path(directory) / "library")
            settings = _settings(
                Path(directory),
                library_path=fixture.root,
                kobo_sync_mode="hybrid",
            )
            initialize_companion_db(settings.companion_db_path)
            device_token = create_device_token(settings.companion_db_path, "Clara")

            with patch("calibre_kobo_companion.server.proxy_kobo_binary_get") as proxy:
                response = handle_get(
                    (
                        f"/kobo/{device_token.token}/{fixture.books[0].uuid}"
                        "/300/400/false/image.jpg"
                    ),
                    settings,
                )

        self.assertEqual(response.status, 200)
        proxy.assert_not_called()

    def test_hybrid_cover_endpoint_proxies_unknown_official_image_id(self) -> None:
        with TemporaryDirectory() as directory:
            fixture = create_calibre_fixture_library(Path(directory) / "library")
            settings = _settings(
                Path(directory),
                library_path=fixture.root,
                kobo_sync_mode="hybrid",
            )
            initialize_companion_db(settings.companion_db_path)
            device_token = create_device_token(settings.companion_db_path, "Clara")

            with patch(
                "calibre_kobo_companion.server.proxy_kobo_binary_get",
                return_value=KoboBinaryProxyResponse(
                    status=200,
                    body=b"official-cover",
                    headers={"Content-Type": "image/jpeg"},
                ),
            ) as proxy:
                response = handle_get(
                    (
                        f"/kobo/{device_token.token}/official-image-id"
                        "/355/530/80/false/image.jpg"
                    ),
                    settings,
                    {
                        "Authorization": "Bearer official-token",
                        "X-Kobo-AppVersion": "4.38.23697",
                    },
                )

        self.assertEqual(response.status, 200)
        self.assertEqual(response.body, b"official-cover")
        self.assertEqual(response.content_type, "image/jpeg")
        proxy.assert_called_once()
        self.assertEqual(
            proxy.call_args.args[0],
            "https://cdn.kobo.com/book-images/official-image-id/355/530/80/false/image.jpg",
        )
        self.assertEqual(proxy.call_args.args[1]["Authorization"], "Bearer official-token")
        self.assertEqual(proxy.call_args.args[1]["X-Kobo-AppVersion"], "4.38.23697")

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
    kobo_sync_mode: str = "local",
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
        kobo_sync_mode=kobo_sync_mode,
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
