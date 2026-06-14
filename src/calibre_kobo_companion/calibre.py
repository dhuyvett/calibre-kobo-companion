from __future__ import annotations

from contextlib import closing
from dataclasses import dataclass
from pathlib import Path
import sqlite3


class CalibreLibraryError(RuntimeError):
    """Raised when a Calibre library cannot be read safely."""


class UnsafeCalibrePath(CalibreLibraryError):
    """Raised when Calibre metadata points outside the library root."""


@dataclass(frozen=True)
class CalibreFormat:
    format: str
    name: str
    uncompressed_size: int
    path: Path


@dataclass(frozen=True)
class CalibreBook:
    id: int
    uuid: str
    title: str
    sort: str | None
    authors: tuple[str, ...]
    description: str | None
    publisher: str | None
    series: str | None
    series_index: float
    language: str | None
    timestamp: str
    pubdate: str | None
    last_modified: str
    relative_path: Path
    absolute_path: Path
    cover_path: Path | None
    formats: tuple[CalibreFormat, ...]


class CalibreLibrary:
    def __init__(self, root: Path):
        self.root = root.expanduser().resolve()
        self.metadata_db_path = self.root / "metadata.db"

    def connect(self) -> sqlite3.Connection:
        uri = f"{self.metadata_db_path.as_uri()}?mode=ro"
        connection = sqlite3.connect(uri, uri=True)
        connection.row_factory = sqlite3.Row
        return connection

    def list_books(self) -> tuple[CalibreBook, ...]:
        with closing(self.connect()) as connection:
            rows = connection.execute(
                """
                SELECT
                  books.id,
                  books.uuid,
                  books.title,
                  books.sort,
                  books.timestamp,
                  books.pubdate,
                  books.series_index,
                  books.path,
                  books.has_cover,
                  books.last_modified,
                  (
                    SELECT group_concat(authors.name, char(31))
                    FROM books_authors_link
                    JOIN authors ON authors.id = books_authors_link.author
                    WHERE books_authors_link.book = books.id
                    ORDER BY books_authors_link.id
                  ) AS authors,
                  comments.text AS description,
                  publishers.name AS publisher,
                  series.name AS series,
                  languages.lang_code AS language,
                  data.format,
                  data.name AS data_name,
                  data.uncompressed_size
                FROM books
                LEFT JOIN comments ON comments.book = books.id
                LEFT JOIN books_publishers_link
                  ON books_publishers_link.book = books.id
                LEFT JOIN publishers
                  ON publishers.id = books_publishers_link.publisher
                LEFT JOIN books_series_link ON books_series_link.book = books.id
                LEFT JOIN series ON series.id = books_series_link.series
                LEFT JOIN books_languages_link
                  ON books_languages_link.book = books.id
                LEFT JOIN languages
                  ON languages.id = books_languages_link.lang_code
                LEFT JOIN data ON data.book = books.id
                ORDER BY books.id, data.format
                """
            ).fetchall()

        return self._books_from_rows(rows)

    def get_book_by_uuid(self, uuid: str) -> CalibreBook | None:
        for book in self.list_books():
            if book.uuid == uuid:
                return book
        return None

    def get_book_by_id(self, book_id: int) -> CalibreBook | None:
        for book in self.list_books():
            if book.id == book_id:
                return book
        return None

    def resolve_library_path(self, relative_path: str | Path) -> Path:
        relative = Path(relative_path)
        if relative.is_absolute():
            raise UnsafeCalibrePath(f"path is absolute: {relative}")

        resolved = (self.root / relative).resolve()
        try:
            resolved.relative_to(self.root)
        except ValueError as exc:
            raise UnsafeCalibrePath(f"path escapes library root: {relative}") from exc
        return resolved

    def _books_from_rows(self, rows: list[sqlite3.Row]) -> tuple[CalibreBook, ...]:
        books: list[CalibreBook] = []
        current_id: int | None = None
        current_rows: list[sqlite3.Row] = []

        for row in rows:
            if current_id is not None and row["id"] != current_id:
                books.append(self._book_from_rows(current_rows))
                current_rows = []

            current_id = row["id"]
            current_rows.append(row)

        if current_rows:
            books.append(self._book_from_rows(current_rows))

        return tuple(books)

    def _book_from_rows(self, rows: list[sqlite3.Row]) -> CalibreBook:
        first = rows[0]
        relative_path = Path(first["path"])
        absolute_path = self.resolve_library_path(relative_path)
        cover_path = (
            self.resolve_library_path(relative_path / "cover.jpg")
            if first["has_cover"]
            else None
        )

        formats = []
        for row in rows:
            if row["format"] is None:
                continue
            format_name = row["format"].upper()
            formats.append(
                CalibreFormat(
                    format=format_name,
                    name=row["data_name"],
                    uncompressed_size=row["uncompressed_size"],
                    path=self.resolve_library_path(
                        relative_path / f"{row['data_name']}.{format_name.lower()}"
                    ),
                )
            )

        return CalibreBook(
            id=first["id"],
            uuid=first["uuid"],
            title=first["title"],
            sort=first["sort"],
            authors=_split_authors(first["authors"]),
            description=first["description"],
            publisher=first["publisher"],
            series=first["series"],
            series_index=first["series_index"],
            language=first["language"],
            timestamp=first["timestamp"],
            pubdate=first["pubdate"],
            last_modified=first["last_modified"],
            relative_path=relative_path,
            absolute_path=absolute_path,
            cover_path=cover_path,
            formats=tuple(formats),
        )


def _split_authors(value: str | None) -> tuple[str, ...]:
    if not value:
        return ()
    return tuple(author for author in value.split(chr(31)) if author)
