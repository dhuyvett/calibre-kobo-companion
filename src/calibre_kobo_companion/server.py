from __future__ import annotations

from http import HTTPStatus
from http.server import BaseHTTPRequestHandler, ThreadingHTTPServer
import json
from socketserver import BaseServer
from dataclasses import dataclass, field
from typing import Any, Mapping
from urllib.parse import urlparse

from .calibre import CalibreLibrary
from .config import Settings
from .db import is_device_token_active
from .kobo import (
    book_metadata,
    build_library_sync_payload,
    decode_sync_token,
)


@dataclass(frozen=True)
class JsonResponse:
    status: HTTPStatus
    payload: dict[str, Any]
    headers: dict[str, str] = field(default_factory=dict)


def handle_get(
    path: str,
    settings: Settings | None = None,
    headers: Mapping[str, str] | None = None,
) -> JsonResponse:
    if path == "/health":
        return JsonResponse(
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
                return JsonResponse(HTTPStatus.OK, _initialization_payload(settings, token))
            if resource_path == "/v1/library/sync":
                return _library_sync_response(settings, token, headers)
            metadata_prefix = "/v1/library/"
            metadata_suffix = "/metadata"
            if resource_path.startswith(metadata_prefix) and resource_path.endswith(
                metadata_suffix
            ):
                book_uuid = resource_path[
                    len(metadata_prefix) : -len(metadata_suffix)
                ]
                return _book_metadata_response(settings, token, book_uuid)
    return JsonResponse(HTTPStatus.NOT_FOUND, {"error": "not_found"})


def handle_post(path: str, settings: Settings) -> JsonResponse:
    route = _parse_kobo_route(path)
    if route is None:
        return JsonResponse(HTTPStatus.NOT_FOUND, {"error": "not_found"})

    token, resource_path = route
    auth_error = _validate_kobo_token(settings, token)
    if auth_error is not None:
        return auth_error

    if resource_path in {"/v1/auth/device", "/v1/auth/refresh"}:
        return JsonResponse(
            HTTPStatus.OK,
            {
                "AccessToken": f"dummy-{token}",
                "RefreshToken": f"dummy-refresh-{token}",
                "TokenType": "Bearer",
            },
        )

    return JsonResponse(HTTPStatus.NOT_FOUND, {"error": "not_found"})


class CompanionServer(ThreadingHTTPServer):
    daemon_threads = True

    def __init__(self, server_address: tuple[str, int], settings: Settings):
        self.settings = settings
        super().__init__(server_address, CompanionRequestHandler)


class CompanionRequestHandler(BaseHTTPRequestHandler):
    server: CompanionServer

    def do_GET(self) -> None:
        response = handle_get(self.path, self.server.settings, self.headers)
        self._send_json(response)

    def do_POST(self) -> None:
        self._discard_request_body()
        response = handle_post(self.path, self.server.settings)
        self._send_json(response)

    def log_message(self, format: str, *args: Any) -> None:
        return

    def _send_json(self, response: JsonResponse) -> None:
        body = json.dumps(response.payload, sort_keys=True).encode("utf-8")
        self.send_response(response.status)
        self.send_header("Content-Type", "application/json; charset=utf-8")
        self.send_header("Content-Length", str(len(body)))
        for name, value in response.headers.items():
            self.send_header(name, value)
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
) -> JsonResponse | None:
    if is_device_token_active(settings.companion_db_path, token):
        return None
    return JsonResponse(HTTPStatus.UNAUTHORIZED, {"error": "unauthorized"})


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


def _library_sync_response(
    settings: Settings,
    token: str,
    headers: Mapping[str, str] | None,
) -> JsonResponse:
    library = CalibreLibrary(settings.calibre_library_path)
    sync_token = decode_sync_token(_header_value(headers, "x-kobo-synctoken"))
    payload, response_headers = build_library_sync_payload(
        library.list_books(),
        settings,
        token,
        sync_token,
    )
    return JsonResponse(HTTPStatus.OK, payload, response_headers)


def _book_metadata_response(
    settings: Settings,
    token: str,
    book_uuid: str,
) -> JsonResponse:
    library = CalibreLibrary(settings.calibre_library_path)
    book = library.get_book_by_uuid(book_uuid)
    if book is None:
        return JsonResponse(HTTPStatus.NOT_FOUND, {"error": "not_found"})
    metadata = book_metadata(book, settings, token)
    if not metadata:
        return JsonResponse(HTTPStatus.NOT_FOUND, {"error": "not_found"})
    return JsonResponse(HTTPStatus.OK, metadata)


def _header_value(headers: Mapping[str, str] | None, name: str) -> str | None:
    if headers is None:
        return None
    for header_name, value in headers.items():
        if header_name.lower() == name:
            return value
    return None
