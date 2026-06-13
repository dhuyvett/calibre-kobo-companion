from __future__ import annotations

import json
from unittest import TestCase

from calibre_kobo_companion.server import handle_get


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
