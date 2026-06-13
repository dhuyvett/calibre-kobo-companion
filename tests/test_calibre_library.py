from __future__ import annotations

import sqlite3
from pathlib import Path
from tempfile import TemporaryDirectory
from unittest import TestCase

from calibre_fixture import create_calibre_fixture_library
from calibre_kobo_companion.calibre import CalibreLibrary, UnsafeCalibrePath


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

            with library.connect() as connection:
                count = connection.execute("SELECT count(*) FROM books").fetchone()[0]
                with self.assertRaises(sqlite3.OperationalError):
                    connection.execute("CREATE TABLE should_not_write (id INTEGER)")

        self.assertEqual(count, 2)

    def test_resolve_library_path_rejects_parent_traversal(self) -> None:
        with TemporaryDirectory() as directory:
            fixture = create_calibre_fixture_library(Path(directory))
            library = CalibreLibrary(fixture.root)

            with self.assertRaises(UnsafeCalibrePath):
                library.resolve_library_path("../outside.epub")

    def test_list_books_rejects_metadata_path_outside_library(self) -> None:
        with TemporaryDirectory() as directory:
            fixture = create_calibre_fixture_library(Path(directory))
            with sqlite3.connect(fixture.metadata_db_path) as connection:
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
