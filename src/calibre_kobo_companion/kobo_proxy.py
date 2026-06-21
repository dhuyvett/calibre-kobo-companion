from __future__ import annotations

from dataclasses import dataclass
import json
from typing import Any, Mapping
from urllib.error import HTTPError, URLError
from urllib.request import Request, urlopen

from .config import Settings


HOP_BY_HOP_REQUEST_HEADERS = {
    "accept-encoding",
    "connection",
    "content-length",
    "expect",
    "host",
    "keep-alive",
    "proxy-authenticate",
    "proxy-authorization",
    "te",
    "trailer",
    "transfer-encoding",
    "upgrade",
}


class KoboStoreUnavailable(RuntimeError):
    """Raised when the Kobo Store API cannot be reached."""


@dataclass(frozen=True)
class KoboProxyResponse:
    status: int
    payload: Any
    headers: dict[str, str]
    body: bytes | None = None


@dataclass(frozen=True)
class KoboBinaryProxyResponse:
    status: int
    body: bytes
    headers: dict[str, str]


def proxy_kobo_get(
    resource_path: str,
    query: str,
    headers: Mapping[str, str] | None,
    settings: Settings,
    *,
    sync_token: str | None = None,
) -> KoboProxyResponse:
    return proxy_kobo_request(
        "GET",
        resource_path,
        query,
        headers,
        settings,
        sync_token=sync_token,
    )


def proxy_kobo_request(
    method: str,
    resource_path: str,
    query: str,
    headers: Mapping[str, str] | None,
    settings: Settings,
    *,
    payload: Mapping[str, Any] | None = None,
    sync_token: str | None = None,
    body: bytes | None = None,
) -> KoboProxyResponse:
    url = _proxy_url(settings.kobo_store_api_url, resource_path, query)
    request_headers = _forward_headers(headers)
    if sync_token:
        request_headers["x-kobo-synctoken"] = sync_token
    request_body = body
    if request_body is None and payload is not None:
        request_body = json.dumps(payload, separators=(",", ":")).encode("utf-8")
        request_headers.setdefault("Content-Type", "application/json")
    request = Request(url, data=request_body, headers=request_headers, method=method)

    try:
        with urlopen(request, timeout=settings.kobo_proxy_timeout_seconds) as response:
            body = response.read()
            return KoboProxyResponse(
                status=response.status,
                payload=_decode_payload(body),
                headers=dict(response.headers.items()),
                body=body,
            )
    except HTTPError as exc:
        body = exc.read()
        return KoboProxyResponse(
            status=exc.code,
            payload=_decode_payload(body),
            headers=dict(exc.headers.items()),
            body=body,
        )
    except URLError as exc:
        raise KoboStoreUnavailable(str(exc)) from exc


def proxy_kobo_binary_get(
    url: str,
    headers: Mapping[str, str] | None,
    settings: Settings,
) -> KoboBinaryProxyResponse:
    request = Request(url, headers=_forward_headers(headers), method="GET")
    try:
        with urlopen(request, timeout=settings.kobo_proxy_timeout_seconds) as response:
            return KoboBinaryProxyResponse(
                status=response.status,
                body=response.read(),
                headers=dict(response.headers.items()),
            )
    except HTTPError as exc:
        return KoboBinaryProxyResponse(
            status=exc.code,
            body=exc.read(),
            headers=dict(exc.headers.items()),
        )
    except URLError as exc:
        raise KoboStoreUnavailable(str(exc)) from exc


def _proxy_url(base_url: str, resource_path: str, query: str) -> str:
    normalized_path = resource_path if resource_path.startswith("/") else f"/{resource_path}"
    url = f"{base_url.rstrip('/')}{normalized_path}"
    if query:
        return f"{url}?{query}"
    return url


def _forward_headers(headers: Mapping[str, str] | None) -> dict[str, str]:
    if headers is None:
        return {}
    forwarded: dict[str, str] = {}
    for name, value in headers.items():
        if name.lower() not in HOP_BY_HOP_REQUEST_HEADERS:
            forwarded[name] = value
    return forwarded


def _decode_payload(body: bytes) -> Any:
    if not body:
        return {}
    try:
        return json.loads(body.decode("utf-8"))
    except (UnicodeDecodeError, json.JSONDecodeError):
        return {"raw": body.decode("utf-8", errors="replace")}
