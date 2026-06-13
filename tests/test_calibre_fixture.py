from __future__ import annotations

import sqlite3
from pathlib import Path
from tempfile import TemporaryDirectory
from unittest import TestCase

from calibre_fixture import create_calibre_fixture_library


class CalibreFixtureTests(TestCase):
    def test_create_calibre_fixture_library_writes_database_and_files(self) -> None:
        with TemporaryDirectory() as directory:
            library = create_calibre_fixture_library(Path(directory))

            self.assertTrue(library.metadata_db_path.exists())
            self.assertEqual(len(library.books), 2)

            for book in library.books:
                book_dir = library.root / book.relative_path
                self.assertTrue((book_dir / "cover.jpg").exists())

                for book_format in book.formats:
                    file_name = f"{book.title} - {book.author}.{book_format.lower()}"
                    self.assertTrue((book_dir / file_name).exists())

    def test_create_calibre_fixture_library_has_expected_metadata(self) -> None:
        with TemporaryDirectory() as directory:
            library = create_calibre_fixture_library(Path(directory))

            with sqlite3.connect(library.metadata_db_path) as connection:
                connection.row_factory = sqlite3.Row
                rows = connection.execute(
                    """
                    SELECT
                      books.title,
                      books.uuid,
                      authors.name AS author_name,
                      comments.text AS description,
                      publishers.name AS publisher_name,
                      series.name AS series_name,
                      languages.lang_code,
                      group_concat(data.format, ',') AS formats
                    FROM books
                    JOIN books_authors_link ON books_authors_link.book = books.id
                    JOIN authors ON authors.id = books_authors_link.author
                    JOIN comments ON comments.book = books.id
                    JOIN books_publishers_link ON books_publishers_link.book = books.id
                    JOIN publishers ON publishers.id = books_publishers_link.publisher
                    JOIN books_series_link ON books_series_link.book = books.id
                    JOIN series ON series.id = books_series_link.series
                    JOIN books_languages_link ON books_languages_link.book = books.id
                    JOIN languages ON languages.id = books_languages_link.lang_code
                    JOIN data ON data.book = books.id
                    GROUP BY books.id
                    ORDER BY books.id
                    """
                ).fetchall()

        self.assertEqual(
            [
                {
                    "title": row["title"],
                    "uuid": row["uuid"],
                    "author_name": row["author_name"],
                    "description": row["description"],
                    "publisher_name": row["publisher_name"],
                    "series_name": row["series_name"],
                    "lang_code": row["lang_code"],
                    "formats": set(row["formats"].split(",")),
                }
                for row in rows
            ],
            [
                {
                    "title": "Existing Kepub",
                    "uuid": "11111111-1111-4111-8111-111111111111",
                    "author_name": "Ada Lovelace",
                    "description": "Existing Kepub description",
                    "publisher_name": "Fixture Publisher",
                    "series_name": "Fixture Series",
                    "lang_code": "eng",
                    "formats": {"EPUB", "KEPUB"},
                },
                {
                    "title": "Epub Only",
                    "uuid": "22222222-2222-4222-8222-222222222222",
                    "author_name": "Grace Hopper",
                    "description": "Epub Only description",
                    "publisher_name": "Fixture Publisher",
                    "series_name": "Fixture Series",
                    "lang_code": "eng",
                    "formats": {"EPUB"},
                },
            ],
        )
