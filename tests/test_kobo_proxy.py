from __future__ import annotations

from unittest import TestCase

from calibre_kobo_companion.kobo_proxy import _forward_headers, _proxy_url


class KoboProxyTests(TestCase):
    def test_proxy_url_preserves_query_parameters(self) -> None:
        self.assertEqual(
            _proxy_url(
                "https://store.example.test/",
                "/v1/library/sync",
                "Filter=ALL&DownloadUrlFilter=Generic",
            ),
            "https://store.example.test/v1/library/sync?Filter=ALL&DownloadUrlFilter=Generic",
        )

    def test_forward_headers_allows_only_kobo_session_headers(self) -> None:
        forwarded = _forward_headers(
            {
                "Authorization": "Bearer secret",
                "If-None-Match": "etag",
                "X-Kobo-AffiliateName": "Kobo",
                "X-Kobo-UserKey": "user-key",
                "X-Kobo-DeviceId": "device-id",
                "X-Kobo-ApiToken": "api-token",
                "X-Kobo-AppVersion": "4.38.23697",
                "X-Kobo-DeviceModel": "Kobo Touch",
                "X-Kobo-DeviceOS": "Linux",
                "X-Kobo-DeviceOSVersion": "2.0",
                "X-Kobo-PlatformId": "00000000-0000-0000-0000-000000000388",
                "User-Agent": "Kobo",
                "Accept-Language": "en-US",
                "Cookie": "private",
                "X-Forwarded-For": "192.0.2.1",
            }
        )

        self.assertEqual(
            forwarded,
            {
                "Authorization": "Bearer secret",
                "If-None-Match": "etag",
                "X-Kobo-AffiliateName": "Kobo",
                "X-Kobo-UserKey": "user-key",
                "X-Kobo-DeviceId": "device-id",
                "X-Kobo-ApiToken": "api-token",
                "X-Kobo-AppVersion": "4.38.23697",
                "X-Kobo-DeviceModel": "Kobo Touch",
                "X-Kobo-DeviceOS": "Linux",
                "X-Kobo-DeviceOSVersion": "2.0",
                "X-Kobo-PlatformId": "00000000-0000-0000-0000-000000000388",
                "User-Agent": "Kobo",
                "Accept-Language": "en-US",
            },
        )
