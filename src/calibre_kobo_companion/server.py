from __future__ import annotations

from http import HTTPStatus
from http.server import BaseHTTPRequestHandler, ThreadingHTTPServer
import json
import logging
from pathlib import Path
import shutil
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


logger = logging.getLogger(__name__)


@dataclass(frozen=True)
class Response:
    status: HTTPStatus
    payload: Any | None = None
    body: bytes | None = None
    file_path: Path | None = None
    content_type: str = "application/json; charset=utf-8"
    headers: dict[str, str] = field(default_factory=dict)


def handle_get(
    path: str,
    settings: Settings | None = None,
    headers: Mapping[str, str] | None = None,
) -> Response:
    if path == "/health":
        return Response(
            HTTPStatus.OK,
            payload={
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
                return Response(HTTPStatus.OK, payload=_initialization_payload(settings, token))
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
            if resource_path.startswith("/download/"):
                return _download_response(settings, resource_path)
            cover_response = _cover_response(settings, resource_path)
            if cover_response is not None:
                return cover_response
            compatibility_response = _compatibility_response(resource_path)
            if compatibility_response is not None:
                return compatibility_response
    return Response(HTTPStatus.NOT_FOUND, payload={"error": "not_found"})


def handle_post(path: str, settings: Settings) -> Response:
    return _handle_kobo_mutating_request(path, settings)


def handle_delete(path: str, settings: Settings) -> Response:
    return _handle_kobo_mutating_request(path, settings)


def _handle_kobo_mutating_request(path: str, settings: Settings) -> Response:
    route = _parse_kobo_route(path)
    if route is None:
        return Response(HTTPStatus.NOT_FOUND, payload={"error": "not_found"})

    token, resource_path = route
    auth_error = _validate_kobo_token(settings, token)
    if auth_error is not None:
        return auth_error

    if resource_path in {"/v1/auth/device", "/v1/auth/refresh"}:
        return Response(
            HTTPStatus.OK,
            payload={
                "AccessToken": f"dummy-{token}",
                "RefreshToken": f"dummy-refresh-{token}",
                "TokenType": "Bearer",
            },
        )

    compatibility_response = _compatibility_response(resource_path)
    if compatibility_response is not None:
        return compatibility_response

    return Response(HTTPStatus.NOT_FOUND, payload={"error": "not_found"})


class CompanionServer(ThreadingHTTPServer):
    daemon_threads = True

    def __init__(self, server_address: tuple[str, int], settings: Settings):
        self.settings = settings
        super().__init__(server_address, CompanionRequestHandler)


class CompanionRequestHandler(BaseHTTPRequestHandler):
    server: CompanionServer

    def do_GET(self) -> None:
        self._handle_request("GET")

    def do_POST(self) -> None:
        self._handle_request("POST")

    def do_DELETE(self) -> None:
        self._handle_request("DELETE")

    def log_message(self, format: str, *args: Any) -> None:
        logger.info("%s - %s", self.client_address[0], format % args)

    def _handle_request(self, method: str) -> None:
        try:
            if method == "GET":
                response = handle_get(self.path, self.server.settings, self.headers)
            elif method == "POST":
                self._discard_request_body()
                response = handle_post(self.path, self.server.settings)
            elif method == "DELETE":
                self._discard_request_body()
                response = handle_delete(self.path, self.server.settings)
            else:
                response = Response(
                    HTTPStatus.METHOD_NOT_ALLOWED,
                    payload={"error": "method_not_allowed"},
                )
        except Exception:
            logger.exception("%s %s failed", method, self.path)
            response = Response(
                HTTPStatus.INTERNAL_SERVER_ERROR,
                payload={"error": "internal_server_error"},
            )

        logger.info("%s %s -> %s", method, self.path, response.status.value)
        self._send_response(response)

    def _send_response(self, response: Response) -> None:
        body = response.body
        if body is None and response.file_path is None:
            body = json.dumps(response.payload or {}, sort_keys=True).encode("utf-8")
        self.send_response(response.status)
        self.send_header("Content-Type", response.content_type)
        if response.file_path is not None:
            self.send_header("Content-Length", str(response.file_path.stat().st_size))
        else:
            self.send_header("Content-Length", str(len(body or b"")))
        for name, value in response.headers.items():
            self.send_header(name, value)
        self.end_headers()
        if response.file_path is not None:
            with response.file_path.open("rb") as file:
                shutil.copyfileobj(file, self.wfile)
        else:
            self.wfile.write(body or b"")

    def _discard_request_body(self) -> None:
        content_length = int(self.headers.get("Content-Length", "0"))
        if content_length:
            self.rfile.read(content_length)


def create_server(settings: Settings) -> BaseServer:
    return CompanionServer((settings.listen_host, settings.listen_port), settings)


def serve(settings: Settings) -> None:
    logging.basicConfig(
        level=getattr(logging, settings.log_level.upper(), logging.INFO),
        format="%(asctime)s %(levelname)s %(name)s: %(message)s",
    )
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
) -> Response | None:
    if is_device_token_active(settings.companion_db_path, token):
        return None
    return Response(HTTPStatus.UNAUTHORIZED, payload={"error": "unauthorized"})


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
            "assets": f"{base_url}/v1/assets",
            "device_auth": f"{base_url}/v1/auth/device",
            "device_refresh": f"{base_url}/v1/auth/refresh",
            "get_tests_request": f"{base_url}/v1/analytics/gettests",
            "image_host": base_url,
            "image_url_quality_template": (
                f"{base_url}/{{ImageId}}/{{Width}}/{{Height}}/"
                "{Quality}/false/image.jpg"
            ),
            "image_url_template": (
                f"{base_url}/{{ImageId}}/{{Width}}/{{Height}}/false/image.jpg"
            ),
            "library_metadata": f"{base_url}/v1/library/{{Ids}}/metadata",
            "library_sync": f"{base_url}/v1/library/sync",
            "reading_state": f"{base_url}/v1/library/{{Ids}}/state",
            "user_loyalty_benefits": f"{base_url}/v1/user/loyalty/benefits",
            "user_profile": f"{base_url}/v1/user/profile",
            "user_recommendations": f"{base_url}/v1/user/recommendations",
            "user_wishlist": f"{base_url}/v1/user/wishlist",
        }
    }


def _library_sync_response(
    settings: Settings,
    token: str,
    headers: Mapping[str, str] | None,
) -> Response:
    library = CalibreLibrary(settings.calibre_library_path)
    sync_token = decode_sync_token(_header_value(headers, "x-kobo-synctoken"))
    payload, response_headers = build_library_sync_payload(
        library.list_books(),
        settings,
        token,
        sync_token,
    )
    logger.info(
        "Kobo library sync returned %s item(s), continue=%s",
        len(payload),
        response_headers.get("x-kobo-sync") == "continue",
    )
    return Response(HTTPStatus.OK, payload=payload, headers=response_headers)


def _book_metadata_response(
    settings: Settings,
    token: str,
    book_uuid: str,
) -> Response:
    library = CalibreLibrary(settings.calibre_library_path)
    book = library.get_book_by_uuid(book_uuid)
    if book is None:
        return Response(HTTPStatus.NOT_FOUND, payload={"error": "not_found"})
    metadata = book_metadata(book, settings, token)
    if not metadata:
        return Response(HTTPStatus.NOT_FOUND, payload={"error": "not_found"})
    return Response(HTTPStatus.OK, payload=[metadata])


def _download_response(settings: Settings, resource_path: str) -> Response:
    parts = resource_path.split("/")
    if len(parts) != 4:
        return Response(HTTPStatus.NOT_FOUND, payload={"error": "not_found"})
    try:
        book_id = int(parts[2])
    except ValueError:
        return Response(HTTPStatus.NOT_FOUND, payload={"error": "not_found"})

    requested_format = parts[3].upper()
    if requested_format not in {"EPUB", "KEPUB"}:
        return Response(HTTPStatus.NOT_FOUND, payload={"error": "not_found"})

    library = CalibreLibrary(settings.calibre_library_path)
    book = library.get_book_by_id(book_id)
    if book is None:
        return Response(HTTPStatus.NOT_FOUND, payload={"error": "not_found"})

    for book_format in book.formats:
        if book_format.format == requested_format and book_format.path.is_file():
            file_name = f"{book.title}.{requested_format.lower()}"
            return Response(
                HTTPStatus.OK,
                file_path=book_format.path,
                content_type=_ebook_content_type(requested_format),
                headers={
                    "Content-Disposition": f'attachment; filename="{file_name}"',
                    "X-Content-Type-Options": "nosniff",
                },
            )
    return Response(HTTPStatus.NOT_FOUND, payload={"error": "not_found"})


def _cover_response(settings: Settings, resource_path: str) -> Response | None:
    parts = resource_path.strip("/").split("/")
    if len(parts) == 5:
        image_id, width, height, false_literal, image_file = parts
    elif len(parts) == 6:
        image_id, width, height, _quality, false_literal, image_file = parts
    else:
        return None
    if false_literal != "false" or image_file != "image.jpg":
        return None
    if not width.isdigit() or not height.isdigit():
        return Response(HTTPStatus.NOT_FOUND, payload={"error": "not_found"})

    library = CalibreLibrary(settings.calibre_library_path)
    for book in library.list_books():
        if _image_id_matches_book(image_id, book.uuid):
            if book.cover_path is not None and book.cover_path.is_file():
                return Response(
                    HTTPStatus.OK,
                    file_path=book.cover_path,
                    content_type="image/jpeg",
                    headers={"X-Content-Type-Options": "nosniff"},
                )
            return Response(HTTPStatus.NOT_FOUND, payload={"error": "not_found"})
    return Response(HTTPStatus.NOT_FOUND, payload={"error": "not_found"})


def _compatibility_response(resource_path: str) -> Response | None:
    if resource_path == "/v1/user/profile":
        return Response(
            HTTPStatus.OK,
            payload={
                "UserDisplayName": "Calibre Kobo Companion",
                "UserId": "calibre-kobo-companion",
            },
        )
    if resource_path == "/v1/user/loyalty/benefits":
        return Response(HTTPStatus.OK, payload={"Benefits": {}})
    if resource_path == "/v1/analytics/gettests":
        return Response(
            HTTPStatus.OK,
            payload={"Result": "Success", "TestKey": "", "Tests": {}},
        )
    if resource_path == "/v1/assets":
        return Response(HTTPStatus.OK, payload={"Assets": []})
    if resource_path in {"/v1/affiliate", "/v1/deals"}:
        return Response(HTTPStatus.OK, payload={})
    if resource_path.startswith("/v1/products"):
        return Response(HTTPStatus.OK, payload={})
    if _is_read_only_library_mutation(resource_path):
        return Response(HTTPStatus.OK, payload={})
    if resource_path.startswith(("/v1/user/loyalty/", "/v1/analytics/")):
        return Response(HTTPStatus.OK, payload={})
    if resource_path in {"/v1/user/wishlist", "/v1/user/recommendations"}:
        return Response(HTTPStatus.OK, payload={})
    return None


def _is_read_only_library_mutation(resource_path: str) -> bool:
    if resource_path.startswith("/v1/library/tags/"):
        return True
    if resource_path.startswith("/v1/library/") and not resource_path.endswith(
        "/metadata"
    ):
        return True
    return False


def _header_value(headers: Mapping[str, str] | None, name: str) -> str | None:
    if headers is None:
        return None
    for header_name, value in headers.items():
        if header_name.lower() == name:
            return value
    return None


def _ebook_content_type(format_name: str) -> str:
    if format_name == "EPUB":
        return "application/epub+zip"
    return "application/vnd.kobo.kepub+zip"


def _image_id_matches_book(image_id: str, book_uuid: str) -> bool:
    return image_id == book_uuid or image_id.startswith(f"{book_uuid}-")
