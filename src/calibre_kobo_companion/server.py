from __future__ import annotations

from http import HTTPStatus
from http.server import BaseHTTPRequestHandler, ThreadingHTTPServer
import json
from socketserver import BaseServer
from typing import Any
from urllib.parse import urlparse

from .config import Settings
from .db import is_device_token_active


def handle_get(
    path: str,
    settings: Settings | None = None,
) -> tuple[HTTPStatus, dict[str, Any]]:
    if path == "/health":
        return (
            HTTPStatus.OK,
            {
                "status": "ok",
                "service": "calibre-kobo-companion",
            },
        )
    if settings is not None:
        route = _parse_kobo_route(path)
        if route is not None:
            token, resource_path = route
            auth_error = _validate_kobo_token(settings, token)
            if auth_error is not None:
                return auth_error
            if resource_path == "/v1/initialization":
                return HTTPStatus.OK, _initialization_payload(settings, token)
    return HTTPStatus.NOT_FOUND, {"error": "not_found"}


def handle_post(path: str, settings: Settings) -> tuple[HTTPStatus, dict[str, Any]]:
    route = _parse_kobo_route(path)
    if route is None:
        return HTTPStatus.NOT_FOUND, {"error": "not_found"}

    token, resource_path = route
    auth_error = _validate_kobo_token(settings, token)
    if auth_error is not None:
        return auth_error

    if resource_path in {"/v1/auth/device", "/v1/auth/refresh"}:
        return (
            HTTPStatus.OK,
            {
                "AccessToken": f"dummy-{token}",
                "RefreshToken": f"dummy-refresh-{token}",
                "TokenType": "Bearer",
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
        status, payload = handle_get(self.path, self.server.settings)
        self._send_json(status, payload)

    def do_POST(self) -> None:
        self._discard_request_body()
        status, payload = handle_post(self.path, self.server.settings)
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

    def _discard_request_body(self) -> None:
        content_length = int(self.headers.get("Content-Length", "0"))
        if content_length:
            self.rfile.read(content_length)


def create_server(settings: Settings) -> BaseServer:
    return CompanionServer((settings.listen_host, settings.listen_port), settings)


def serve(settings: Settings) -> None:
    server = create_server(settings)
    print(f"Serving on http://{settings.listen_host}:{settings.listen_port}")
    try:
        server.serve_forever()
    finally:
        server.server_close()


def _parse_kobo_route(path: str) -> tuple[str, str] | None:
    parsed_path = urlparse(path).path
    parts = parsed_path.split("/")
    if len(parts) < 4 or parts[1] != "kobo" or not parts[2]:
        return None

    token = parts[2]
    resource_path = "/" + "/".join(parts[3:])
    return token, resource_path


def _validate_kobo_token(
    settings: Settings,
    token: str,
) -> tuple[HTTPStatus, dict[str, Any]] | None:
    if is_device_token_active(settings.companion_db_path, token):
        return None
    return HTTPStatus.UNAUTHORIZED, {"error": "unauthorized"}


def _initialization_payload(settings: Settings, token: str) -> dict[str, Any]:
    base_url = f"{settings.public_base_url}/kobo/{token}"
    return {
        "Resources": {
            "Account": f"{base_url}/v1/user/profile",
            "AuthDevice": f"{base_url}/v1/auth/device",
            "AuthRefresh": f"{base_url}/v1/auth/refresh",
            "BookMetadata": f"{base_url}/v1/library/{{RevisionId}}/metadata",
            "Image": f"{base_url}/{{ImageId}}/{{Width}}/{{Height}}/false/image.jpg",
            "LibrarySync": f"{base_url}/v1/library/sync",
        }
    }
