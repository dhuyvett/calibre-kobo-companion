from __future__ import annotations

from contextlib import closing
import sqlite3
from pathlib import Path
from tempfile import TemporaryDirectory
from unittest import TestCase

from calibre_fixture import create_calibre_fixture_library
from calibre_kobo_companion.calibre import (
    CalibreLibrary,
    CalibreLibraryUnavailable,
    UnsafeCalibrePath,
)


class CalibreLibraryTests(TestCase):
    def test_list_books_reads_fixture_metadata_and_paths(self) -> None:
        with TemporaryDirectory() as directory:
            fixture = create_calibre_fixture_library(Path(directory))
            library = CalibreLibrary(fixture.root)

            books = library.list_books()

            self.assertEqual([book.title for book in books], ["Existing Kepub", "Epub Only"])
            self.assertEqual(books[0].uuid, "11111111-1111-4111-8111-111111111111")
            self.assertEqual(books[0].authors, ("Ada Lovelace",))
            self.assertEqual(books[0].description, "Existing Kepub description")
            self.assertEqual(books[0].publisher, "Fixture Publisher")
            self.assertEqual(books[0].series, "Fixture Series")
            self.assertEqual(books[0].series_index, 1.0)
            self.assertEqual(books[0].language, "eng")
            self.assertEqual(
                {book_format.format for book_format in books[0].formats},
                {"EPUB", "KEPUB"},
            )
            self.assertEqual(
                {book_format.format for book_format in books[1].formats},
                {"EPUB"},
            )

            for book in books:
                self.assertTrue(book.absolute_path.exists())
                self.assertTrue(book.cover_path is not None)
                self.assertTrue(book.cover_path.exists())
                for book_format in book.formats:
                    self.assertTrue(book_format.path.exists())
                    self.assertGreater(book_format.uncompressed_size, 0)

    def test_connect_opens_metadata_database_read_only(self) -> None:
        with TemporaryDirectory() as directory:
            fixture = create_calibre_fixture_library(Path(directory))
            library = CalibreLibrary(fixture.root)

            with closing(library.connect()) as connection:
                count = connection.execute("SELECT count(*) FROM books").fetchone()[0]
                with self.assertRaises(sqlite3.OperationalError):
                    connection.execute("CREATE TABLE should_not_write (id INTEGER)")

        self.assertEqual(count, 2)

    def test_targeted_book_lookup_reads_one_book(self) -> None:
        with TemporaryDirectory() as directory:
            fixture = create_calibre_fixture_library(Path(directory))
            library = CalibreLibrary(fixture.root)

            book_by_uuid = library.get_book_by_uuid(fixture.books[1].uuid)
            book_by_id = library.get_book_by_id(fixture.books[1].id)

        self.assertIsNotNone(book_by_uuid)
        self.assertIsNotNone(book_by_id)
        self.assertEqual(book_by_uuid.title, "Epub Only")
        self.assertEqual(book_by_id.uuid, fixture.books[1].uuid)

    def test_targeted_cover_lookup_returns_cover_path(self) -> None:
        with TemporaryDirectory() as directory:
            fixture = create_calibre_fixture_library(Path(directory))
            library = CalibreLibrary(fixture.root)

            cover_path = library.get_cover_by_uuid(fixture.books[0].uuid)

        self.assertEqual(cover_path, fixture.root / fixture.books[0].relative_path / "cover.jpg")

    def test_book_uuids_exist_returns_known_ids(self) -> None:
        with TemporaryDirectory() as directory:
            fixture = create_calibre_fixture_library(Path(directory))
            library = CalibreLibrary(fixture.root)

            existing = library.book_uuids_exist([fixture.books[0].uuid, "official-book"])

        self.assertEqual(existing, {fixture.books[0].uuid})

    def test_get_books_by_uuid_returns_known_books(self) -> None:
        with TemporaryDirectory() as directory:
            fixture = create_calibre_fixture_library(Path(directory))
            library = CalibreLibrary(fixture.root)

            books = library.get_books_by_uuid([fixture.books[0].uuid, "official-book"])

        self.assertEqual(set(books), {fixture.books[0].uuid})
        self.assertEqual(books[fixture.books[0].uuid].title, "Existing Kepub")

    def test_connect_reports_missing_metadata_database_as_unavailable(self) -> None:
        with TemporaryDirectory() as directory:
            library = CalibreLibrary(Path(directory) / "missing-library")

            with self.assertRaisesRegex(CalibreLibraryUnavailable, "metadata database"):
                library.connect()

    def test_resolve_library_path_rejects_parent_traversal(self) -> None:
        with TemporaryDirectory() as directory:
            fixture = create_calibre_fixture_library(Path(directory))
            library = CalibreLibrary(fixture.root)

            with self.assertRaises(UnsafeCalibrePath):
                library.resolve_library_path("../outside.epub")

    def test_list_books_rejects_metadata_path_outside_library(self) -> None:
        with TemporaryDirectory() as directory:
            fixture = create_calibre_fixture_library(Path(directory))
            with closing(sqlite3.connect(fixture.metadata_db_path)) as connection:
                connection.execute(
                    """
                    INSERT INTO books (
                      id, title, sort, timestamp, pubdate, series_index,
                      author_sort, path, uuid, has_cover, last_modified
                    )
                    VALUES (?, ?, ?, ?, ?, ?, ?, ?, ?, ?, ?)
                    """,
                    (
                        99,
                        "Escaping Book",
                        "Escaping Book",
                        "2024-03-01 00:00:00+00:00",
                        None,
                        1.0,
                        "Nobody",
                        "../outside",
                        "99999999-9999-4999-8999-999999999999",
                        0,
                        "2024-03-01 00:00:00+00:00",
                    ),
                )
                connection.commit()

            library = CalibreLibrary(fixture.root)

            with self.assertRaises(UnsafeCalibrePath):
                library.list_books()
