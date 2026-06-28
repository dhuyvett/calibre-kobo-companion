from __future__ import annotations

from http import HTTPStatus
from http.server import BaseHTTPRequestHandler, ThreadingHTTPServer
import json
import logging
from pathlib import Path
import secrets
import shutil
import sqlite3
import ssl
from socketserver import BaseServer
from dataclasses import dataclass, field
from typing import Any, Mapping
from urllib.parse import quote, urlparse
from uuid import uuid4

from .calibre import CalibreBook, CalibreFormat, CalibreLibrary, CalibreLibraryError
from .config import ConfigError, Settings
from .db import is_device_token_active
from .kobo import (
    HybridSyncToken,
    book_metadata,
    build_library_sync_payload,
    decode_hybrid_sync_token,
    decode_sync_token,
    encode_hybrid_sync_token,
)
from .kobo_proxy import (
    KoboBinaryProxyResponse,
    KoboProxyResponse,
    KoboStoreUnavailable,
    proxy_kobo_binary_get,
    proxy_kobo_get,
    proxy_kobo_request,
)
from .kepub import KepubConversionError, convert_epub_to_kepub


logger = logging.getLogger(__name__)
KOBO_STORE_API_URL = "https://storeapi.kobo.com"
KOBO_IMAGEHOST_URL = "https://cdn.kobo.com/book-images"
KOBO_AUTHORIZE_URL = "https://authorize.kobo.com"
KOBO_WEB_URL = "https://www.kobo.com"
_CALIBRE_UNAVAILABLE_EXCEPTIONS = (CalibreLibraryError, OSError, sqlite3.Error)


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
                return _initialization_response(settings, token, headers)
            if resource_path == "/v1/library/sync":
                return _library_sync_response(settings, token, path, headers)
            metadata_prefix = "/v1/library/"
            metadata_suffix = "/metadata"
            if resource_path.startswith(metadata_prefix) and resource_path.endswith(
                metadata_suffix
            ):
                book_uuid = resource_path[
                    len(metadata_prefix) : -len(metadata_suffix)
                ]
                return _book_metadata_response(settings, token, book_uuid, path, headers)
            if resource_path.startswith("/download/"):
                return _download_response(settings, resource_path)
            cover_response = _cover_response(settings, resource_path, headers)
            if cover_response is not None:
                return cover_response
            if settings.kobo_sync_mode == "hybrid":
                hybrid_response = _hybrid_get_response(
                    settings,
                    resource_path,
                    path,
                    headers,
                )
                if hybrid_response is not None:
                    return hybrid_response
            compatibility_response = _compatibility_response(resource_path)
            if compatibility_response is not None:
                return compatibility_response
    return Response(HTTPStatus.NOT_FOUND, payload={"error": "not_found"})


def handle_post(
    path: str,
    settings: Settings,
    payload: Mapping[str, Any] | None = None,
    headers: Mapping[str, str] | None = None,
    body: bytes | None = None,
) -> Response:
    return _handle_kobo_mutating_request("POST", path, settings, payload, headers, body)


def handle_delete(
    path: str,
    settings: Settings,
    headers: Mapping[str, str] | None = None,
    body: bytes | None = None,
) -> Response:
    return _handle_kobo_mutating_request(
        "DELETE",
        path,
        settings,
        headers=headers,
        body=body,
    )


def handle_put(
    path: str,
    settings: Settings,
    payload: Mapping[str, Any] | None = None,
    headers: Mapping[str, str] | None = None,
    body: bytes | None = None,
) -> Response:
    return _handle_kobo_mutating_request("PUT", path, settings, payload, headers, body)


def _handle_kobo_mutating_request(
    method: str,
    path: str,
    settings: Settings,
    payload: Mapping[str, Any] | None = None,
    headers: Mapping[str, str] | None = None,
    body: bytes | None = None,
) -> Response:
    route = _parse_kobo_route(path)
    if route is None:
        return Response(HTTPStatus.NOT_FOUND, payload={"error": "not_found"})

    token, resource_path = route
    auth_error = _validate_kobo_token(settings, token)
    if auth_error is not None:
        return auth_error

    if resource_path in {"/v1/auth/device", "/v1/auth/refresh"}:
        if settings.kobo_sync_mode == "hybrid":
            return _hybrid_auth_response(
                method,
                settings,
                resource_path,
                headers,
                payload,
                body,
            )
        return Response(
            HTTPStatus.OK,
            payload=_auth_payload(payload),
        )

    if settings.kobo_sync_mode == "hybrid":
        if _is_read_only_library_mutation(resource_path):
            hybrid_response = _hybrid_library_mutation_response(
                method,
                settings,
                resource_path,
                path,
                headers,
                payload,
                body,
            )
            if hybrid_response is not None:
                return hybrid_response
        return _hybrid_mutating_proxy_response(
            method,
            settings,
            resource_path,
            path,
            headers,
            payload,
            body,
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

    def do_PUT(self) -> None:
        self._handle_request("PUT")

    def log_message(self, format: str, *args: Any) -> None:
        logger.info("%s - %s", self.client_address[0], format % args)

    def _handle_request(self, method: str) -> None:
        try:
            if method == "GET":
                response = handle_get(self.path, self.server.settings, self.headers)
            elif method == "POST":
                body = self._read_request_body()
                response = handle_post(
                    self.path,
                    self.server.settings,
                    _json_payload_from_body(body),
                    self.headers,
                    body or None,
                )
            elif method == "DELETE":
                body = self._read_request_body()
                response = handle_delete(
                    self.path,
                    self.server.settings,
                    self.headers,
                    body or None,
                )
            elif method == "PUT":
                body = self._read_request_body()
                response = handle_put(
                    self.path,
                    self.server.settings,
                    _json_payload_from_body(body),
                    self.headers,
                    body or None,
                )
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

    def _read_request_body(self) -> bytes:
        content_length = int(self.headers.get("Content-Length", "0"))
        if not content_length:
            return b""
        return self.rfile.read(content_length)


def create_server(settings: Settings) -> BaseServer:
    server = CompanionServer((settings.listen_host, settings.listen_port), settings)
    if settings.tls_enabled:
        server.socket = _tls_context(settings).wrap_socket(
            server.socket,
            server_side=True,
        )
    return server


def serve(settings: Settings) -> None:
    logging.basicConfig(
        level=getattr(logging, settings.log_level.upper(), logging.INFO),
        format="%(asctime)s %(levelname)s %(name)s: %(message)s",
    )
    server = create_server(settings)
    scheme = "https" if settings.tls_enabled else "http"
    print(f"Serving on {scheme}://{settings.listen_host}:{settings.listen_port}")
    try:
        server.serve_forever()
    finally:
        server.server_close()


def _tls_context(settings: Settings) -> ssl.SSLContext:
    if settings.tls_cert_path is None or settings.tls_key_path is None:
        raise ConfigError("TLS_CERT_PATH and TLS_KEY_PATH must be configured together")
    if not settings.tls_cert_path.is_file():
        raise ConfigError(f"TLS_CERT_PATH is not a readable file: {settings.tls_cert_path}")
    if not settings.tls_key_path.is_file():
        raise ConfigError(f"TLS_KEY_PATH is not a readable file: {settings.tls_key_path}")

    context = ssl.SSLContext(ssl.PROTOCOL_TLS_SERVER)
    try:
        context.load_cert_chain(
            certfile=settings.tls_cert_path,
            keyfile=settings.tls_key_path,
        )
    except OSError as exc:
        raise ConfigError(f"TLS certificate or key could not be read: {exc}") from exc
    except ssl.SSLError as exc:
        raise ConfigError(f"TLS certificate and key are invalid or mismatched: {exc}") from exc
    return context


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


def _calibre_unavailable_response(
    settings: Settings,
    operation: str,
    exc: BaseException,
) -> Response:
    logger.warning(
        "Calibre library unavailable during %s at %s: %s",
        operation,
        settings.calibre_library_path,
        exc,
    )
    return Response(
        HTTPStatus.SERVICE_UNAVAILABLE,
        payload={"error": "calibre_library_unavailable"},
    )


def _auth_payload(payload: Mapping[str, Any] | None) -> dict[str, str]:
    user_key = ""
    if payload is not None and isinstance(payload.get("UserKey"), str):
        user_key = payload["UserKey"]
    return {
        "AccessToken": secrets.token_urlsafe(24),
        "RefreshToken": secrets.token_urlsafe(24),
        "TokenType": "Bearer",
        "TrackingId": str(uuid4()),
        "UserKey": user_key,
    }


def _json_payload_from_body(body: bytes) -> Mapping[str, Any] | None:
    if not body:
        return None
    try:
        payload = json.loads(body.decode("utf-8"))
    except (UnicodeDecodeError, json.JSONDecodeError):
        return None
    if isinstance(payload, Mapping):
        return payload
    return None


def _hybrid_auth_response(
    method: str,
    settings: Settings,
    resource_path: str,
    headers: Mapping[str, str] | None,
    payload: Mapping[str, Any] | None,
    body: bytes | None = None,
) -> Response:
    try:
        kobo_response = proxy_kobo_request(
            method,
            resource_path,
            "",
            _headers_without_dummy_api_token(headers),
            settings,
            payload=payload,
            body=body,
        )
    except KoboStoreUnavailable as exc:
        logger.warning("Kobo Store unavailable during hybrid auth: %s", exc)
        return Response(
            HTTPStatus.BAD_GATEWAY,
            payload={"error": "kobo_store_unavailable"},
        )
    return _proxy_response(kobo_response)


def _initialization_payload(settings: Settings, token: str) -> dict[str, Any]:
    return _patched_initialization_payload(settings, token, _native_kobo_resources())


def _initialization_response(
    settings: Settings,
    token: str,
    headers: Mapping[str, str] | None,
) -> Response:
    if settings.kobo_sync_mode != "hybrid":
        return Response(
            HTTPStatus.OK,
            payload=_initialization_payload(settings, token),
            headers={"x-kobo-apitoken": "e30="},
        )

    try:
        kobo_response = proxy_kobo_get(
            "/v1/initialization",
            "",
            _headers_without_dummy_api_token(headers),
            settings,
        )
    except KoboStoreUnavailable as exc:
        logger.warning("Kobo Store unavailable during hybrid initialization: %s", exc)
        return _hybrid_local_initialization_response(settings, token)

    if kobo_response.status >= 400:
        logger.warning(
            "Kobo initialization returned %s during hybrid initialization; "
            "falling back to local resource patching; request_auth=%s",
            kobo_response.status,
            _auth_header_presence(headers),
        )
        return _hybrid_local_initialization_response(settings, token)

    upstream_payload = (
        kobo_response.payload
        if isinstance(kobo_response.payload, Mapping)
        else {}
    )
    upstream_resources = upstream_payload.get("Resources", {})
    resources = (
        dict(upstream_resources)
        if isinstance(upstream_resources, Mapping)
        else {}
    )
    payload = dict(upstream_payload)
    payload.update(_patched_initialization_payload(settings, token, resources))

    response_headers = _hybrid_passthrough_headers(kobo_response.headers)
    api_token = _case_insensitive_header(kobo_response.headers, "x-kobo-apitoken")
    if api_token is not None:
        response_headers["x-kobo-apitoken"] = api_token
    return Response(
        _http_status(kobo_response.status),
        payload=payload,
        headers=response_headers,
    )


def _hybrid_local_initialization_response(settings: Settings, token: str) -> Response:
    return Response(
        HTTPStatus.OK,
        payload=_initialization_payload(settings, token),
    )


def _patched_initialization_payload(
    settings: Settings,
    token: str,
    resources: Mapping[str, Any],
) -> dict[str, Any]:
    base_url = f"{settings.public_base_url}/kobo/{token}"
    patched_resources = dict(resources)
    patched_resources.update(
        {
            "BookMetadata": f"{base_url}/v1/library/{{RevisionId}}/metadata",
            "Image": f"{base_url}/{{ImageId}}/{{Width}}/{{Height}}/false/image.jpg",
            "LibrarySync": f"{base_url}/v1/library/sync",
            "delete_entitlement": f"{base_url}/v1/library/{{Ids}}",
            "delete_tag": f"{base_url}/v1/library/tags/{{TagId}}",
            "delete_tag_items": f"{base_url}/v1/library/tags/{{TagId}}/items/delete",
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
            "rename_tag": f"{base_url}/v1/library/tags/{{TagId}}",
            "tag_items": f"{base_url}/v1/library/tags/{{TagId}}/Items",
            "tags": f"{base_url}/v1/library/tags",
        }
    )
    return {
        "Resources": patched_resources
    }


def _native_kobo_resources() -> dict[str, Any]:
    return {
        "Account": f"{KOBO_STORE_API_URL}/v1/user/profile",
        "AuthDevice": f"{KOBO_STORE_API_URL}/v1/auth/device",
        "AuthRefresh": f"{KOBO_STORE_API_URL}/v1/auth/refresh",
        "account_page": f"{KOBO_WEB_URL}/account/settings",
        "add_device": f"{KOBO_STORE_API_URL}/v1/user/add-device",
        "add_entitlement": f"{KOBO_STORE_API_URL}/v1/library/{{RevisionIds}}",
        "affiliaterequest": f"{KOBO_STORE_API_URL}/v1/affiliate",
        "assets": f"{KOBO_STORE_API_URL}/v1/assets",
        "browse_history": f"{KOBO_STORE_API_URL}/v1/user/browsehistory",
        "categories": f"{KOBO_STORE_API_URL}/v1/categories",
        "configuration_data": f"{KOBO_STORE_API_URL}/v1/configuration",
        "deals": f"{KOBO_STORE_API_URL}/v1/deals",
        "device_auth": f"{KOBO_STORE_API_URL}/v1/auth/device",
        "device_refresh": f"{KOBO_STORE_API_URL}/v1/auth/refresh",
        "dictionary_host": "https://ereaderfiles.kobo.com",
        "discovery_host": "https://discovery.kobobooks.com",
        "ereaderdevices": f"{KOBO_STORE_API_URL}/v2/products/EReaderDeviceFeeds",
        "exchange_auth": f"{KOBO_STORE_API_URL}/v1/auth/exchange",
        "featured_lists": f"{KOBO_STORE_API_URL}/v1/products/featured",
        "funnel_metrics": f"{KOBO_STORE_API_URL}/v1/funnelmetrics",
        "get_download_keys": f"{KOBO_STORE_API_URL}/v1/library/downloadkeys",
        "get_download_link": f"{KOBO_STORE_API_URL}/v1/library/downloadlink",
        "get_tests_request": f"{KOBO_STORE_API_URL}/v1/analytics/gettests",
        "help_page": f"{KOBO_WEB_URL}/help",
        "kobo_display_price": "True",
        "kobo_nativeborrow_enabled": "True",
        "kobo_onestorelibrary_enabled": "False",
        "kobo_privacyCentre_url": f"{KOBO_WEB_URL}/privacy",
        "kobo_redeem_enabled": "True",
        "kobo_superpoints_enabled": "True",
        "kobo_wishlist_enabled": "True",
        "library_book": f"{KOBO_STORE_API_URL}/v1/user/library/books/{{LibraryItemId}}",
        "library_items": f"{KOBO_STORE_API_URL}/v1/user/library",
        "library_prices": f"{KOBO_STORE_API_URL}/v1/user/library/previews/prices",
        "library_search": f"{KOBO_STORE_API_URL}/v1/library/search",
        "oauth_host": "https://oauth.kobo.com",
        "personalizedrecommendations": (
            f"{KOBO_STORE_API_URL}/v2/users/personalizedrecommendations"
        ),
        "post_analytics_event": f"{KOBO_STORE_API_URL}/v1/analytics/event",
        "product_prices": f"{KOBO_STORE_API_URL}/v1/products/{{ProductIds}}/prices",
        "product_recommendations": (
            f"{KOBO_STORE_API_URL}/v1/products/{{ProductId}}/recommendations"
        ),
        "product_reviews": f"{KOBO_STORE_API_URL}/v1/products/{{ProductIds}}/reviews",
        "products": f"{KOBO_STORE_API_URL}/v1/products",
        "productsv2": f"{KOBO_STORE_API_URL}/v2/products",
        "quickbuy_checkout": f"{KOBO_STORE_API_URL}/v1/store/quickbuy/{{PurchaseId}}/checkout",
        "quickbuy_create": f"{KOBO_STORE_API_URL}/v1/store/quickbuy/purchase",
        "rakuten_token_exchange": f"{KOBO_STORE_API_URL}/v1/auth/rakuten_token_exchange",
        "reading_services_host": "https://readingservices.kobo.com",
        "registration_page": f"{KOBO_AUTHORIZE_URL}/signup?returnUrl=http://kobo.com/",
        "sign_in_page": f"{KOBO_AUTHORIZE_URL}/signin?returnUrl=http://kobo.com/",
        "social_authorization_host": "https://social.kobobooks.com:8443",
        "social_host": "https://social.kobobooks.com",
        "store_home": "www.kobo.com/{region}/{language}",
        "store_host": "www.kobo.com",
        "store_search": f"{KOBO_WEB_URL}/{{region}}/{{language}}/Search?Query={{query}}",
        "taste_profile": f"{KOBO_STORE_API_URL}/v1/products/tasteprofile",
        "use_one_store": "True",
        "user_loyalty_benefits": f"{KOBO_STORE_API_URL}/v1/user/loyalty/benefits",
        "user_platform": f"{KOBO_STORE_API_URL}/v1/user/platform",
        "user_profile": f"{KOBO_STORE_API_URL}/v1/user/profile",
        "user_ratings": f"{KOBO_STORE_API_URL}/v1/user/ratings",
        "user_recommendations": f"{KOBO_STORE_API_URL}/v1/user/recommendations",
        "user_reviews": f"{KOBO_STORE_API_URL}/v1/user/reviews",
        "user_wishlist": f"{KOBO_STORE_API_URL}/v1/user/wishlist",
        "userguide_host": "https://ereaderfiles.kobo.com",
    }


def _library_sync_response(
    settings: Settings,
    token: str,
    request_path: str,
    headers: Mapping[str, str] | None,
) -> Response:
    if settings.kobo_sync_mode == "hybrid":
        return _hybrid_library_sync_response(settings, token, request_path, headers)
    return _local_library_sync_response(settings, token, headers)


def _local_library_sync_response(
    settings: Settings,
    token: str,
    headers: Mapping[str, str] | None,
) -> Response:
    library = CalibreLibrary(settings.calibre_library_path)
    sync_token = decode_sync_token(_header_value(headers, "x-kobo-synctoken"))
    try:
        books = library.list_books()
    except _CALIBRE_UNAVAILABLE_EXCEPTIONS as exc:
        return _calibre_unavailable_response(settings, "library sync", exc)
    payload, response_headers = build_library_sync_payload(books, settings, token, sync_token)
    logger.info(
        "Kobo library sync returned %s item(s), continue=%s",
        len(payload),
        response_headers.get("x-kobo-sync") == "continue",
    )
    return Response(HTTPStatus.OK, payload=payload, headers=response_headers)


def _hybrid_library_sync_response(
    settings: Settings,
    token: str,
    request_path: str,
    headers: Mapping[str, str] | None,
) -> Response:
    request_sync_token = _header_value(headers, "x-kobo-synctoken")
    hybrid_token = decode_hybrid_sync_token(request_sync_token)
    parsed_path = urlparse(request_path)

    try:
        kobo_response = proxy_kobo_get(
            "/v1/library/sync",
            parsed_path.query,
            _headers_without_dummy_api_token(headers),
            settings,
            sync_token=hybrid_token.kobo,
        )
    except KoboStoreUnavailable as exc:
        logger.warning("Kobo Store unavailable during hybrid sync: %s", exc)
        return Response(
            HTTPStatus.BAD_GATEWAY,
            payload={"error": "kobo_store_unavailable"},
        )

    if (
        kobo_response.status
        in {HTTPStatus.BAD_REQUEST, HTTPStatus.UNAUTHORIZED, HTTPStatus.FORBIDDEN}
        and hybrid_token.kobo is not None
    ):
        logger.warning(
            "Kobo Store rejected hybrid sync token with %s; retrying official sync "
            "without it; request_auth=%s",
            kobo_response.status,
            _auth_header_presence(headers),
        )
        try:
            kobo_response = proxy_kobo_get(
                "/v1/library/sync",
                parsed_path.query,
                _headers_without_dummy_api_token(headers),
                settings,
            )
        except KoboStoreUnavailable as exc:
            logger.warning("Kobo Store unavailable during hybrid sync retry: %s", exc)
            return Response(
                HTTPStatus.BAD_GATEWAY,
                payload={"error": "kobo_store_unavailable"},
            )

    if kobo_response.status >= 400:
        if kobo_response.status in {HTTPStatus.UNAUTHORIZED, HTTPStatus.FORBIDDEN}:
            logger.warning(
                "Kobo Store rejected hybrid sync with %s; returning local sync only; "
                "official Kobo and OverDrive items will not sync until Kobo auth "
                "is valid; request_auth=%s",
                kobo_response.status,
                _auth_header_presence(headers),
            )
            return _local_library_sync_response(settings, token, headers)
        return Response(
            _http_status(kobo_response.status),
            payload=kobo_response.payload,
            headers=_hybrid_passthrough_headers(kobo_response.headers),
        )

    official_payload = (
        list(kobo_response.payload)
        if isinstance(kobo_response.payload, list)
        else kobo_response.payload
    )
    if not isinstance(official_payload, list):
        return Response(
            _http_status(kobo_response.status),
            payload=official_payload,
            headers=_hybrid_passthrough_headers(kobo_response.headers),
        )

    local_payload: list[dict[str, Any]] = []
    local_headers: dict[str, str] = {}
    try:
        books = CalibreLibrary(settings.calibre_library_path).list_books()
    except _CALIBRE_UNAVAILABLE_EXCEPTIONS as exc:
        if settings.hybrid_sync_require_local_library:
            return _calibre_unavailable_response(settings, "hybrid local sync", exc)
        logger.warning(
            "Calibre library unavailable during hybrid local sync at %s: %s",
            settings.calibre_library_path,
            exc,
        )
    else:
        local_payload, local_headers = build_library_sync_payload(
            books,
            settings,
            token,
            hybrid_token.local,
        )

    next_kobo_token = _case_insensitive_header(kobo_response.headers, "x-kobo-synctoken")
    next_local_token = decode_sync_token(local_headers.get("x-kobo-synctoken"))
    response_headers = _hybrid_passthrough_headers(kobo_response.headers)
    response_headers["x-kobo-synctoken"] = encode_hybrid_sync_token(
        HybridSyncToken(kobo=next_kobo_token, local=next_local_token)
    )
    if (
        _case_insensitive_header(kobo_response.headers, "x-kobo-sync") == "continue"
        or local_headers.get("x-kobo-sync") == "continue"
    ):
        response_headers["x-kobo-sync"] = "continue"
    else:
        response_headers.pop("x-kobo-sync", None)

    payload = official_payload + local_payload
    logger.info(
        "Hybrid Kobo library sync returned %s official item(s), %s local item(s), continue=%s",
        len(official_payload),
        len(local_payload),
        response_headers.get("x-kobo-sync") == "continue",
    )
    return Response(HTTPStatus.OK, payload=payload, headers=response_headers)


def _book_metadata_response(
    settings: Settings,
    token: str,
    book_ids_value: str,
    request_path: str,
    headers: Mapping[str, str] | None,
) -> Response:
    book_ids = _split_kobo_ids(book_ids_value)
    if settings.kobo_sync_mode == "hybrid":
        return _hybrid_book_metadata_response(
            settings,
            token,
            book_ids,
            request_path,
            headers,
        )
    if len(book_ids) != 1:
        return Response(HTTPStatus.NOT_FOUND, payload={"error": "not_found"})
    return _local_book_metadata_response(settings, token, book_ids[0])


def _local_book_metadata_response(
    settings: Settings,
    token: str,
    book_uuid: str,
) -> Response:
    library = CalibreLibrary(settings.calibre_library_path)
    try:
        book = library.get_book_by_uuid(book_uuid)
    except _CALIBRE_UNAVAILABLE_EXCEPTIONS as exc:
        return _calibre_unavailable_response(settings, "book metadata", exc)
    if book is None:
        return Response(HTTPStatus.NOT_FOUND, payload={"error": "not_found"})
    metadata = book_metadata(book, settings, token)
    if not metadata:
        return Response(HTTPStatus.NOT_FOUND, payload={"error": "not_found"})
    return Response(HTTPStatus.OK, payload=[metadata])


def _hybrid_book_metadata_response(
    settings: Settings,
    token: str,
    book_ids: list[str],
    request_path: str,
    headers: Mapping[str, str] | None,
) -> Response:
    if not book_ids:
        return Response(HTTPStatus.NOT_FOUND, payload={"error": "not_found"})

    local_payload: list[dict[str, Any]] = []
    official_ids: list[str] = []
    try:
        library = CalibreLibrary(settings.calibre_library_path)
        local_books = library.get_books_by_uuid(book_ids)
        for book_id in book_ids:
            book = local_books.get(book_id)
            if book is None:
                official_ids.append(book_id)
                continue
            metadata = book_metadata(book, settings, token)
            if metadata:
                local_payload.append(metadata)
    except _CALIBRE_UNAVAILABLE_EXCEPTIONS as exc:
        if settings.hybrid_sync_require_local_library:
            return _calibre_unavailable_response(settings, "hybrid book metadata", exc)
        logger.warning(
            "Calibre library unavailable during hybrid book metadata at %s: %s",
            settings.calibre_library_path,
            exc,
        )
        official_ids = book_ids
        local_payload = []

    if not official_ids:
        if not local_payload:
            return Response(HTTPStatus.NOT_FOUND, payload={"error": "not_found"})
        return Response(HTTPStatus.OK, payload=local_payload)

    parsed_path = urlparse(request_path)
    official_resource_path = f"/v1/library/{','.join(official_ids)}/metadata"
    try:
        kobo_response = proxy_kobo_get(
            official_resource_path,
            parsed_path.query,
            _headers_without_dummy_api_token(headers),
            settings,
        )
    except KoboStoreUnavailable as exc:
        logger.warning("Kobo Store unavailable during hybrid metadata: %s", exc)
        if local_payload:
            return Response(HTTPStatus.OK, payload=local_payload)
        return Response(
            HTTPStatus.BAD_GATEWAY,
            payload={"error": "kobo_store_unavailable"},
        )

    if kobo_response.status >= 400:
        if local_payload and kobo_response.status == HTTPStatus.NOT_FOUND:
            return Response(HTTPStatus.OK, payload=local_payload)
        return _proxy_response(kobo_response)

    if isinstance(kobo_response.payload, list):
        payload = local_payload + kobo_response.payload
    elif local_payload:
        payload = local_payload
    else:
        payload = kobo_response.payload
    return Response(
        _http_status(kobo_response.status),
        payload=payload,
        headers=_hybrid_passthrough_headers(kobo_response.headers),
    )


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
    try:
        book = library.get_book_by_id(book_id)
    except _CALIBRE_UNAVAILABLE_EXCEPTIONS as exc:
        return _calibre_unavailable_response(settings, "download", exc)
    if book is None:
        return Response(HTTPStatus.NOT_FOUND, payload={"error": "not_found"})

    for book_format in book.formats:
        if book_format.format == requested_format and book_format.path.is_file():
            return _ebook_file_response(book, book_format, requested_format)
    if requested_format == "KEPUB":
        epub_format = _book_format(book, "EPUB")
        if epub_format is not None:
            if not settings.enable_kepubify or settings.kepubify_path is None:
                return _ebook_file_response(book, epub_format, "EPUB")
            try:
                conversion = convert_epub_to_kepub(book, epub_format, settings)
            except KepubConversionError:
                logger.exception("KEPUB conversion failed for book %s", book.id)
                return Response(
                    HTTPStatus.INTERNAL_SERVER_ERROR,
                    payload={"error": "kepub_conversion_failed"},
                )
            file_name = f"{book.title}.kepub.epub"
            return Response(
                HTTPStatus.OK,
                file_path=conversion.path,
                content_type=_ebook_content_type(requested_format),
                headers={
                    "Content-Disposition": _content_disposition(file_name),
                    "X-Content-Type-Options": "nosniff",
                },
            )
    return Response(HTTPStatus.NOT_FOUND, payload={"error": "not_found"})


def _cover_response(
    settings: Settings,
    resource_path: str,
    headers: Mapping[str, str] | None,
) -> Response | None:
    parts = resource_path.strip("/").split("/")
    if len(parts) == 5:
        image_id, width, height, false_literal, image_file = parts
        quality = None
    elif len(parts) == 6:
        image_id, width, height, quality, false_literal, image_file = parts
    else:
        return None
    if false_literal != "false" or image_file != "image.jpg":
        return None
    if not width.isdigit() or not height.isdigit() or (
        quality is not None and not quality.isdigit()
    ):
        return Response(HTTPStatus.NOT_FOUND, payload={"error": "not_found"})

    image_book_uuid = _book_uuid_from_image_id(image_id)
    library = CalibreLibrary(settings.calibre_library_path)
    try:
        cover_path = library.get_cover_by_uuid(image_book_uuid)
    except _CALIBRE_UNAVAILABLE_EXCEPTIONS as exc:
        return _calibre_unavailable_response(settings, "cover", exc)
    if cover_path is not None:
        if cover_path.is_file():
            return Response(
                HTTPStatus.OK,
                file_path=cover_path,
                content_type="image/jpeg",
                headers={"X-Content-Type-Options": "nosniff"},
            )
        return Response(HTTPStatus.NOT_FOUND, payload={"error": "not_found"})
    if settings.kobo_sync_mode == "hybrid":
        return _hybrid_cover_response(settings, image_id, width, height, quality, headers)
    return Response(HTTPStatus.NOT_FOUND, payload={"error": "not_found"})


def _hybrid_cover_response(
    settings: Settings,
    image_id: str,
    width: str,
    height: str,
    quality: str | None,
    headers: Mapping[str, str] | None,
) -> Response:
    url = _kobo_image_url(image_id, width, height, quality)
    try:
        kobo_response = proxy_kobo_binary_get(
            url,
            _headers_without_dummy_api_token(headers),
            settings,
        )
    except KoboStoreUnavailable as exc:
        logger.warning("Kobo image host unavailable during hybrid cover lookup: %s", exc)
        return Response(
            HTTPStatus.BAD_GATEWAY,
            payload={"error": "kobo_image_unavailable"},
        )
    return _binary_proxy_response(kobo_response)


def _kobo_image_url(
    image_id: str,
    width: str,
    height: str,
    quality: str | None,
) -> str:
    if quality is None:
        return f"{KOBO_IMAGEHOST_URL}/{image_id}/{width}/{height}/false/image.jpg"
    return f"{KOBO_IMAGEHOST_URL}/{image_id}/{width}/{height}/{quality}/false/image.jpg"


def _hybrid_library_mutation_response(
    method: str,
    settings: Settings,
    resource_path: str,
    request_path: str,
    headers: Mapping[str, str] | None,
    payload: Mapping[str, Any] | None,
    body: bytes | None = None,
) -> Response | None:
    routed_ids = _library_mutation_ids(resource_path)
    if routed_ids is None:
        return None
    book_ids, suffix = routed_ids
    if not book_ids:
        return Response(HTTPStatus.OK, payload={})

    try:
        official_ids = _unknown_local_book_ids(settings, book_ids)
    except _CALIBRE_UNAVAILABLE_EXCEPTIONS as exc:
        if settings.hybrid_sync_require_local_library:
            return _calibre_unavailable_response(settings, "hybrid library mutation", exc)
        logger.warning(
            "Calibre library unavailable during hybrid library mutation at %s: %s",
            settings.calibre_library_path,
            exc,
        )
        official_ids = book_ids

    if not official_ids:
        return Response(HTTPStatus.OK, payload={})

    parsed_path = urlparse(request_path)
    official_resource_path = f"/v1/library/{','.join(official_ids)}{suffix}"
    try:
        kobo_response = proxy_kobo_request(
            method,
            official_resource_path,
            parsed_path.query,
            _headers_without_dummy_api_token(headers),
            settings,
            payload=payload,
            body=body,
        )
    except KoboStoreUnavailable as exc:
        logger.warning("Kobo Store unavailable during hybrid library mutation: %s", exc)
        return Response(
            HTTPStatus.BAD_GATEWAY,
            payload={"error": "kobo_store_unavailable"},
        )
    return _proxy_response(kobo_response)


def _hybrid_get_response(
    settings: Settings,
    resource_path: str,
    request_path: str,
    headers: Mapping[str, str] | None,
) -> Response | None:
    parsed_path = urlparse(request_path)
    try:
        kobo_response = proxy_kobo_get(
            resource_path,
            parsed_path.query,
            _headers_without_dummy_api_token(headers),
            settings,
        )
    except KoboStoreUnavailable as exc:
        logger.warning("Kobo Store unavailable during hybrid GET %s: %s", resource_path, exc)
        return Response(
            HTTPStatus.BAD_GATEWAY,
            payload={"error": "kobo_store_unavailable"},
        )
    if kobo_response.status >= 400:
        logger.warning(
            "Kobo Store returned %s for hybrid GET %s; request_auth=%s",
            kobo_response.status,
            resource_path,
            _auth_header_presence(headers),
        )
    if (
        kobo_response.status == HTTPStatus.FORBIDDEN
        and resource_path in _HYBRID_AUTH_REFRESH_TRIGGER_PATHS
    ):
        return Response(
            HTTPStatus.UNAUTHORIZED,
            payload=kobo_response.payload,
            headers=_hybrid_passthrough_headers(kobo_response.headers),
        )
    return _proxy_response(kobo_response)


def _hybrid_mutating_proxy_response(
    method: str,
    settings: Settings,
    resource_path: str,
    request_path: str,
    headers: Mapping[str, str] | None,
    payload: Mapping[str, Any] | None,
    body: bytes | None = None,
) -> Response:
    parsed_path = urlparse(request_path)
    try:
        kobo_response = proxy_kobo_request(
            method,
            resource_path,
            parsed_path.query,
            _headers_without_dummy_api_token(headers),
            settings,
            payload=payload,
            body=body,
        )
    except KoboStoreUnavailable as exc:
        logger.warning(
            "Kobo Store unavailable during hybrid %s %s: %s",
            method,
            resource_path,
            exc,
        )
        return Response(
            HTTPStatus.BAD_GATEWAY,
            payload={"error": "kobo_store_unavailable"},
        )
    if kobo_response.status >= 400:
        logger.warning(
            "Kobo Store returned %s for hybrid %s %s; request_auth=%s",
            kobo_response.status,
            method,
            resource_path,
            _auth_header_presence(headers),
        )
    return _proxy_response(kobo_response)


_HYBRID_AUTH_REFRESH_TRIGGER_PATHS = {
    "/v1/user/profile",
    "/v1/user/loyalty/benefits",
    "/v1/deals",
}


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
    if resource_path in {
        "/v1/user/reviews",
        "/v1/user/wishlist",
        "/v1/user/recommendations",
    }:
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


def _library_mutation_ids(resource_path: str) -> tuple[list[str], str] | None:
    if not resource_path.startswith("/v1/library/"):
        return None
    if resource_path.startswith("/v1/library/tags/"):
        return None
    if resource_path.endswith("/metadata"):
        return None

    remainder = resource_path[len("/v1/library/") :]
    suffix = ""
    ids_value = remainder
    if "/" in remainder:
        ids_value, suffix_part = remainder.split("/", 1)
        suffix = f"/{suffix_part}"
    return _split_kobo_ids(ids_value), suffix


def _unknown_local_book_ids(settings: Settings, book_ids: list[str]) -> list[str]:
    library = CalibreLibrary(settings.calibre_library_path)
    local_ids = library.book_uuids_exist(book_ids)
    return [book_id for book_id in book_ids if book_id not in local_ids]


def _split_kobo_ids(value: str) -> list[str]:
    return [
        book_id
        for book_id in (part.strip() for part in value.split(","))
        if book_id
    ]


def _proxy_response(kobo_response: KoboProxyResponse) -> Response:
    if kobo_response.body is not None:
        content_type = _case_insensitive_header(kobo_response.headers, "content-type")
        return Response(
            _http_status(kobo_response.status),
            body=kobo_response.body,
            content_type=content_type or "application/octet-stream",
            headers=_hybrid_passthrough_headers(kobo_response.headers),
        )
    return Response(
        _http_status(kobo_response.status),
        payload=kobo_response.payload,
        headers=_hybrid_passthrough_headers(kobo_response.headers),
    )


def _binary_proxy_response(kobo_response: KoboBinaryProxyResponse) -> Response:
    content_type = _case_insensitive_header(kobo_response.headers, "content-type")
    return Response(
        _http_status(kobo_response.status),
        body=kobo_response.body,
        content_type=content_type or "application/octet-stream",
        headers=_hybrid_passthrough_headers(kobo_response.headers),
    )


def _header_value(headers: Mapping[str, str] | None, name: str) -> str | None:
    if headers is None:
        return None
    for header_name, value in headers.items():
        if header_name.lower() == name:
            return value
    return None


def _headers_without(
    headers: Mapping[str, str] | None,
    excluded_names: set[str],
) -> dict[str, str]:
    if headers is None:
        return {}
    excluded = {name.lower() for name in excluded_names}
    return {
        name: value
        for name, value in headers.items()
        if name.lower() not in excluded
    }


def _headers_without_dummy_api_token(
    headers: Mapping[str, str] | None,
) -> Mapping[str, str] | None:
    if _header_value(headers, "x-kobo-apitoken") == "e30=":
        return _headers_without(headers, {"x-kobo-apitoken"})
    return headers


def _auth_header_presence(headers: Mapping[str, str] | None) -> dict[str, bool]:
    return {
        "authorization": _header_value(headers, "authorization") is not None,
        "x-kobo-apitoken": _header_value(headers, "x-kobo-apitoken") is not None,
        "x-kobo-deviceid": _header_value(headers, "x-kobo-deviceid") is not None,
        "x-kobo-userkey": _header_value(headers, "x-kobo-userkey") is not None,
        "x-kobo-synctoken": _header_value(headers, "x-kobo-synctoken") is not None,
    }


def _case_insensitive_header(headers: Mapping[str, str], name: str) -> str | None:
    for header_name, value in headers.items():
        if header_name.lower() == name.lower():
            return value
    return None


def _hybrid_passthrough_headers(headers: Mapping[str, str]) -> dict[str, str]:
    passthrough = {}
    excluded = {
        "connection",
        "content-encoding",
        "content-length",
        "content-type",
        "date",
        "keep-alive",
        "proxy-authenticate",
        "proxy-authorization",
        "server",
        "te",
        "trailer",
        "transfer-encoding",
        "upgrade",
    }
    for name, value in headers.items():
        if name.lower() not in excluded:
            passthrough[name] = value
    return passthrough


def _http_status(status: int) -> HTTPStatus:
    try:
        return HTTPStatus(status)
    except ValueError:
        return HTTPStatus.BAD_GATEWAY


def _ebook_content_type(format_name: str) -> str:
    if format_name == "EPUB":
        return "application/epub+zip"
    return "application/vnd.kobo.kepub+zip"


def _ebook_file_response(
    book: CalibreBook,
    book_format: CalibreFormat,
    response_format: str,
) -> Response:
    extension = "kepub" if response_format == "KEPUB" else "epub"
    file_name = f"{book.title}.{extension}"
    return Response(
        HTTPStatus.OK,
        file_path=book_format.path,
        content_type=_ebook_content_type(response_format),
        headers={
            "Content-Disposition": _content_disposition(file_name),
            "X-Content-Type-Options": "nosniff",
        },
    )


def _content_disposition(file_name: str) -> str:
    fallback = "".join(
        character
        if 32 <= ord(character) < 127 and character not in {'"', "\\", "/", ";"}
        else "_"
        for character in file_name
    ).strip(" ._")
    if not fallback:
        fallback = "download"
    encoded = quote(file_name, safe="")
    return f'attachment; filename="{fallback}"; filename*=UTF-8\'\'{encoded}'


def _book_format(book: CalibreBook, format_name: str) -> CalibreFormat | None:
    for book_format in book.formats:
        if book_format.format == format_name and book_format.path.is_file():
            return book_format
    return None


def _book_uuid_from_image_id(image_id: str) -> str:
    return image_id[:36]
