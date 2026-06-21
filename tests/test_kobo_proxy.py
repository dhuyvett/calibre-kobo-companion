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

    def test_forward_headers_filters_only_hop_by_hop_headers(self) -> None:
        forwarded = _forward_headers(
            {
                "Authorization": "Bearer secret",
                "Accept-Encoding": "gzip, deflate",
                "Connection": "keep-alive",
                "Content-Length": "123",
                "Expect": "100-continue",
                "If-None-Match": "etag",
                "Host": "local.example.test",
                "X-Kobo-AffiliateName": "Kobo",
                "X-Kobo-UserKey": "user-key",
                "X-New-Kobo-Header": "new-value",
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
                "X-New-Kobo-Header": "new-value",
                "Cookie": "private",
                "X-Forwarded-For": "192.0.2.1",
            },
        )
