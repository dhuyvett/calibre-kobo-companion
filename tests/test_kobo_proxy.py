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
                "X-Kobo-UserKey": "user-key",
                "X-Kobo-DeviceId": "device-id",
                "X-Kobo-ApiToken": "api-token",
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
                "X-Kobo-UserKey": "user-key",
                "X-Kobo-DeviceId": "device-id",
                "X-Kobo-ApiToken": "api-token",
                "User-Agent": "Kobo",
                "Accept-Language": "en-US",
            },
        )

