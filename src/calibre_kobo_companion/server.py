from __future__ import annotations

from http import HTTPStatus
from http.server import BaseHTTPRequestHandler, ThreadingHTTPServer
import json
from socketserver import BaseServer
from typing import Any

from .config import Settings


def handle_get(path: str) -> tuple[HTTPStatus, dict[str, Any]]:
    if path == "/health":
        return (
            HTTPStatus.OK,
            {
                "status": "ok",
                "service": "calibre-kobo-companion",
            },
        )
    return HTTPStatus.NOT_FOUND, {"error": "not_found"}


class CompanionServer(ThreadingHTTPServer):
    daemon_threads = True

    def __init__(self, server_address: tuple[str, int], settings: Settings):
        self.settings = settings
        super().__init__(server_address, CompanionRequestHandler)


class CompanionRequestHandler(BaseHTTPRequestHandler):
    server: CompanionServer

    def do_GET(self) -> None:
        status, payload = handle_get(self.path)
        self._send_json(status, payload)

    def log_message(self, format: str, *args: Any) -> None:
        return

    def _send_json(self, status: HTTPStatus, payload: dict[str, Any]) -> None:
        body = json.dumps(payload, sort_keys=True).encode("utf-8")
        self.send_response(status)
        self.send_header("Content-Type", "application/json; charset=utf-8")
        self.send_header("Content-Length", str(len(body)))
        self.end_headers()
        self.wfile.write(body)


def create_server(settings: Settings) -> BaseServer:
    return CompanionServer((settings.listen_host, settings.listen_port), settings)


def serve(settings: Settings) -> None:
    server = create_server(settings)
    print(f"Serving on http://{settings.listen_host}:{settings.listen_port}")
    try:
        server.serve_forever()
    finally:
        server.server_close()
