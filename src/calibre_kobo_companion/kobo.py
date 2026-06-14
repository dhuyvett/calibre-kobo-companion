from __future__ import annotations

import base64
from dataclasses import dataclass
from datetime import UTC, datetime
import json
from typing import Any

from .calibre import CalibreBook, CalibreFormat
from .config import Settings


SUPPORTED_FORMATS = ("KEPUB", "EPUB")


@dataclass(frozen=True)
class SyncToken:
    since: str | None = None
    offset: int = 0


def encode_sync_token(token: SyncToken) -> str:
    payload = {
        "since": token.since,
        "offset": token.offset,
    }
    raw = json.dumps(payload, separators=(",", ":"), sort_keys=True).encode("utf-8")
    return base64.urlsafe_b64encode(raw).decode("ascii").rstrip("=")


def decode_sync_token(value: str | None) -> SyncToken:
    if not value:
        return SyncToken()
    try:
        padded = value + ("=" * (-len(value) % 4))
        raw = base64.urlsafe_b64decode(padded.encode("ascii"))
        payload = json.loads(raw.decode("utf-8"))
    except (ValueError, TypeError, json.JSONDecodeError):
        return SyncToken()

    since = payload.get("since")
    offset = payload.get("offset", 0)
    if not isinstance(since, str | type(None)):
        since = None
    if not isinstance(offset, int) or offset < 0:
        offset = 0
    return SyncToken(since=since, offset=offset)


def build_library_sync_payload(
    books: tuple[CalibreBook, ...],
    settings: Settings,
    token: str,
    sync_token: SyncToken,
) -> tuple[dict[str, Any], dict[str, str]]:
    changed_books = tuple(
        book
        for book in books
        if _select_download_format(book) is not None
        and (sync_token.since is None or _changed_timestamp(book) > sync_token.since)
    )
    page = changed_books[
        sync_token.offset : sync_token.offset + settings.kobo_sync_page_size
    ]
    next_offset = sync_token.offset + len(page)
    has_more = next_offset < len(changed_books)

    response_token = SyncToken(
        since=sync_token.since if has_more else _next_since(changed_books, sync_token),
        offset=next_offset if has_more else 0,
    )
    headers = {"x-kobo-synctoken": encode_sync_token(response_token)}
    if has_more:
        headers["x-kobo-sync"] = "continue"

    return (
        {
            "ChangedEntitlements": [],
            "DeletedEntitlements": [],
            "NewEntitlements": [
                {
                    "NewEntitlement": {
                        "BookEntitlement": _book_entitlement(book),
                        "BookMetadata": book_metadata(book, settings, token),
                    }
                }
                for book in page
            ],
        },
        headers,
    )


def book_metadata(
    book: CalibreBook,
    settings: Settings,
    token: str,
) -> dict[str, Any]:
    selected_format = _select_download_format(book)
    if selected_format is None:
        return {}

    base_url = f"{settings.public_base_url}/kobo/{token}"
    contributors = [{"Name": author} for author in book.authors]
    download_urls = [
        {
            "Format": selected_format.format,
            "Size": selected_format.uncompressed_size,
            "Url": (
                f"{base_url}/download/{book.id}/"
                f"{selected_format.format.lower()}"
            ),
        }
    ]

    metadata: dict[str, Any] = {
        "Categories": [],
        "Contributors": contributors,
        "ContributorRoles": ["Author"] if contributors else [],
        "CoverImageId": _cover_image_id(book),
        "CrossRevisionId": book.uuid,
        "CurrentDisplayPrice": "0.00",
        "CurrentLoveDisplayPrice": "0.00",
        "Description": book.description or "",
        "DownloadUrls": download_urls,
        "EntitlementId": book.uuid,
        "Genre": "",
        "Id": book.uuid,
        "IsDownloaded": False,
        "IsSocialEnabled": False,
        "Language": _language_code(book.language),
        "PublicationDate": _kobo_timestamp(book.pubdate or book.timestamp),
        "Publisher": {"Name": book.publisher or ""},
        "RevisionId": book.uuid,
        "Title": book.title,
        "WorkId": book.uuid,
    }
    if book.series:
        metadata["Series"] = {
            "Name": book.series,
            "Number": book.series_index,
            "NumberFloat": book.series_index,
        }
    return metadata


def _book_entitlement(book: CalibreBook) -> dict[str, Any]:
    return {
        "Accessibility": "Full",
        "ActivePeriod": {"From": _kobo_timestamp(book.timestamp)},
        "Created": _kobo_timestamp(book.timestamp),
        "CrossRevisionId": book.uuid,
        "Id": book.uuid,
        "IsHiddenFromArchive": False,
        "IsLocked": False,
        "LastModified": _kobo_timestamp(_changed_timestamp(book)),
        "OriginCategory": "Imported",
        "RevisionId": book.uuid,
        "Status": "Active",
    }


def _select_download_format(book: CalibreBook) -> CalibreFormat | None:
    formats_by_name = {book_format.format: book_format for book_format in book.formats}
    for format_name in SUPPORTED_FORMATS:
        if format_name in formats_by_name:
            return formats_by_name[format_name]
    return None


def _cover_image_id(book: CalibreBook) -> str:
    return f"{book.uuid}-{_changed_timestamp(book).replace(':', '').replace('+', '')}"


def _changed_timestamp(book: CalibreBook) -> str:
    return book.last_modified or book.timestamp


def _next_since(books: tuple[CalibreBook, ...], sync_token: SyncToken) -> str | None:
    if not books:
        return sync_token.since
    return max(_changed_timestamp(book) for book in books)


def _language_code(language: str | None) -> str:
    if not language:
        return "en"
    if language == "eng":
        return "en"
    return language


def _kobo_timestamp(value: str) -> str:
    parsed = _parse_timestamp(value)
    if parsed is None:
        return value
    return parsed.astimezone(UTC).replace(microsecond=0).isoformat().replace("+00:00", "Z")


def _parse_timestamp(value: str) -> datetime | None:
    try:
        return datetime.fromisoformat(value.replace("Z", "+00:00"))
    except ValueError:
        try:
            return datetime.strptime(value, "%Y-%m-%d %H:%M:%S%z")
        except ValueError:
            return None
