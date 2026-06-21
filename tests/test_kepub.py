from __future__ import annotations

import os
from pathlib import Path
from tempfile import TemporaryDirectory
from unittest import TestCase

from calibre_kobo_companion.config import Settings
from calibre_kobo_companion.kepub import prune_kepub_cache


class KepubTests(TestCase):
    def test_prune_kepub_cache_removes_oldest_files_until_under_limit(self) -> None:
        with TemporaryDirectory() as directory:
            root = Path(directory)
            settings = _settings(root, kepub_cache_max_mb=1)
            old_file = _write_cache_file(root, "old-book/old.kepub.epub", size=800_000)
            keep_file = _write_cache_file(root, "new-book/new.kepub.epub", size=400_000)
            os.utime(old_file, (1, 1))
            os.utime(keep_file, (2, 2))

            prune_kepub_cache(settings, keep_path=keep_file)

            self.assertFalse(old_file.exists())
            self.assertTrue(keep_file.exists())

    def test_prune_kepub_cache_keeps_current_file_when_over_limit(self) -> None:
        with TemporaryDirectory() as directory:
            root = Path(directory)
            settings = _settings(root, kepub_cache_max_mb=1)
            keep_file = _write_cache_file(root, "book/current.kepub.epub", size=2_000_000)

            prune_kepub_cache(settings, keep_path=keep_file)

            self.assertTrue(keep_file.exists())

    def test_prune_kepub_cache_is_disabled_when_limit_is_zero(self) -> None:
        with TemporaryDirectory() as directory:
            root = Path(directory)
            settings = _settings(root, kepub_cache_max_mb=0)
            old_file = _write_cache_file(root, "book/old.kepub.epub", size=2_000_000)

            prune_kepub_cache(settings)

            self.assertTrue(old_file.exists())


def _write_cache_file(root: Path, relative_path: str, *, size: int) -> Path:
    path = root / "cache" / "kepub" / relative_path
    path.parent.mkdir(parents=True, exist_ok=True)
    path.write_bytes(b"x" * size)
    return path


def _settings(root: Path, *, kepub_cache_max_mb: int) -> Settings:
    return Settings(
        calibre_library_path=root / "library",
        companion_db_path=root / "companion.db",
        companion_cache_path=root / "cache",
        public_base_url="http://example.test",
        kepub_cache_max_mb=kepub_cache_max_mb,
    )
