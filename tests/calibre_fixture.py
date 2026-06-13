from __future__ import annotations

from dataclasses import dataclass
from pathlib import Path
import sqlite3


SMALL_JPEG = bytes.fromhex(
    "ffd8ffe000104a46494600010101006000600000ffdb004300"
    "0302020302020303030304030304050805050404050a07070608"
    "0c0a0c0c0b0a0b0b0d0e12100d0e110e0b0b10161011131415"
    "15150c0f171816141812141514ffdb0043010304040504050905"
    "0509140d0b0d1414141414141414141414141414141414141414"
    "1414141414141414141414141414141414141414141414141414"
    "141414141414141414ffc0001108000100010301220002110103"
    "1101ffc400140001000000000000000000000000000000000000"
    "0008ffc400141001000000000000000000000000000000000000"
    "0000ffda000c03010002110311003f00b2c001ffd9"
)


@dataclass(frozen=True)
class FixtureBook:
    id: int
    uuid: str
    title: str
    author: str
    relative_path: Path
    formats: tuple[str, ...]


@dataclass(frozen=True)
class CalibreFixtureLibrary:
    root: Path
    metadata_db_path: Path
    books: tuple[FixtureBook, ...]


def create_calibre_fixture_library(root: Path) -> CalibreFixtureLibrary:
    """Create a tiny Calibre-like library for tests.

    The schema intentionally contains only tables and columns this project
    expects to read. Extend it when production queries need more Calibre data.
    """

    root.mkdir(parents=True, exist_ok=True)
    metadata_db_path = root / "metadata.db"
    books = (
        FixtureBook(
            id=1,
            uuid="11111111-1111-4111-8111-111111111111",
            title="Existing Kepub",
            author="Ada Lovelace",
            relative_path=Path("Ada Lovelace") / "Existing Kepub (1)",
            formats=("EPUB", "KEPUB"),
        ),
        FixtureBook(
            id=2,
            uuid="22222222-2222-4222-8222-222222222222",
            title="Epub Only",
            author="Grace Hopper",
            relative_path=Path("Grace Hopper") / "Epub Only (2)",
            formats=("EPUB",),
        ),
    )

    _write_book_files(root, books)
    _write_metadata_db(metadata_db_path, books)

    return CalibreFixtureLibrary(
        root=root,
        metadata_db_path=metadata_db_path,
        books=books,
    )


def _write_book_files(root: Path, books: tuple[FixtureBook, ...]) -> None:
    for book in books:
        book_dir = root / book.relative_path
        book_dir.mkdir(parents=True, exist_ok=True)
        (book_dir / "cover.jpg").write_bytes(SMALL_JPEG)

        for book_format in book.formats:
            name = _data_name(book)
            extension = book_format.lower()
            (book_dir / f"{name}.{extension}").write_bytes(
                f"{book.title} fixture {book_format}\n".encode("utf-8")
            )


def _write_metadata_db(path: Path, books: tuple[FixtureBook, ...]) -> None:
    with sqlite3.connect(path) as connection:
        connection.executescript(
            """
            CREATE TABLE library_id (
              uuid TEXT NOT NULL
            );

            CREATE TABLE books (
              id INTEGER PRIMARY KEY,
              title TEXT NOT NULL,
              sort TEXT,
              timestamp TEXT NOT NULL,
              pubdate TEXT,
              series_index REAL NOT NULL DEFAULT 1.0,
              author_sort TEXT,
              path TEXT NOT NULL,
              uuid TEXT NOT NULL,
              has_cover INTEGER NOT NULL DEFAULT 1,
              last_modified TEXT NOT NULL
            );

            CREATE TABLE data (
              id INTEGER PRIMARY KEY,
              book INTEGER NOT NULL,
              format TEXT NOT NULL,
              uncompressed_size INTEGER NOT NULL,
              name TEXT NOT NULL
            );

            CREATE TABLE authors (
              id INTEGER PRIMARY KEY,
              name TEXT NOT NULL,
              sort TEXT,
              link TEXT NOT NULL DEFAULT ''
            );

            CREATE TABLE books_authors_link (
              id INTEGER PRIMARY KEY,
              book INTEGER NOT NULL,
              author INTEGER NOT NULL
            );

            CREATE TABLE comments (
              id INTEGER PRIMARY KEY,
              book INTEGER NOT NULL,
              text TEXT NOT NULL
            );

            CREATE TABLE publishers (
              id INTEGER PRIMARY KEY,
              name TEXT NOT NULL,
              sort TEXT
            );

            CREATE TABLE books_publishers_link (
              id INTEGER PRIMARY KEY,
              book INTEGER NOT NULL,
              publisher INTEGER NOT NULL
            );

            CREATE TABLE series (
              id INTEGER PRIMARY KEY,
              name TEXT NOT NULL,
              sort TEXT
            );

            CREATE TABLE books_series_link (
              id INTEGER PRIMARY KEY,
              book INTEGER NOT NULL,
              series INTEGER NOT NULL
            );

            CREATE TABLE languages (
              id INTEGER PRIMARY KEY,
              lang_code TEXT NOT NULL
            );

            CREATE TABLE books_languages_link (
              id INTEGER PRIMARY KEY,
              book INTEGER NOT NULL,
              lang_code INTEGER NOT NULL
            );
            """
        )
        connection.execute(
            "INSERT INTO library_id (uuid) VALUES (?)",
            ("aaaaaaaa-bbbb-4ccc-8ddd-eeeeeeeeeeee",),
        )

        for book in books:
            _insert_book(connection, book)

        connection.commit()


def _insert_book(connection: sqlite3.Connection, book: FixtureBook) -> None:
    author_id = book.id
    publisher_id = book.id
    series_id = book.id
    language_id = book.id

    connection.execute(
        """
        INSERT INTO books (
          id, title, sort, timestamp, pubdate, series_index, author_sort,
          path, uuid, has_cover, last_modified
        )
        VALUES (?, ?, ?, ?, ?, ?, ?, ?, ?, ?, ?)
        """,
        (
            book.id,
            book.title,
            book.title,
            f"2024-01-0{book.id} 12:00:00+00:00",
            f"2020-01-0{book.id} 00:00:00+00:00",
            float(book.id),
            book.author,
            book.relative_path.as_posix(),
            book.uuid,
            1,
            f"2024-02-0{book.id} 12:00:00+00:00",
        ),
    )
    connection.execute(
        "INSERT INTO authors (id, name, sort) VALUES (?, ?, ?)",
        (author_id, book.author, book.author),
    )
    connection.execute(
        "INSERT INTO books_authors_link (book, author) VALUES (?, ?)",
        (book.id, author_id),
    )
    connection.execute(
        "INSERT INTO comments (book, text) VALUES (?, ?)",
        (book.id, f"{book.title} description"),
    )
    connection.execute(
        "INSERT INTO publishers (id, name, sort) VALUES (?, ?, ?)",
        (publisher_id, "Fixture Publisher", "Fixture Publisher"),
    )
    connection.execute(
        "INSERT INTO books_publishers_link (book, publisher) VALUES (?, ?)",
        (book.id, publisher_id),
    )
    connection.execute(
        "INSERT INTO series (id, name, sort) VALUES (?, ?, ?)",
        (series_id, "Fixture Series", "Fixture Series"),
    )
    connection.execute(
        "INSERT INTO books_series_link (book, series) VALUES (?, ?)",
        (book.id, series_id),
    )
    connection.execute(
        "INSERT INTO languages (id, lang_code) VALUES (?, ?)",
        (language_id, "eng"),
    )
    connection.execute(
        "INSERT INTO books_languages_link (book, lang_code) VALUES (?, ?)",
        (book.id, language_id),
    )

    for book_format in book.formats:
        name = _data_name(book)
        size = len(f"{book.title} fixture {book_format}\n".encode("utf-8"))
        connection.execute(
            """
            INSERT INTO data (book, format, uncompressed_size, name)
            VALUES (?, ?, ?, ?)
            """,
            (book.id, book_format, size, name),
        )


def _data_name(book: FixtureBook) -> str:
    return f"{book.title} - {book.author}"
