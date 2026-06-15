from __future__ import annotations

from dataclasses import dataclass
from pathlib import Path
import shutil
import subprocess
from tempfile import TemporaryDirectory
from threading import Lock

from .calibre import CalibreBook, CalibreFormat
from .config import Settings


class KepubConversionError(RuntimeError):
    """Raised when an EPUB cannot be converted to KEPUB."""


@dataclass(frozen=True)
class KepubConversion:
    path: Path
    created: bool


_locks_guard = Lock()
_conversion_locks: dict[str, Lock] = {}


def convert_epub_to_kepub(
    book: CalibreBook,
    source_format: CalibreFormat,
    settings: Settings,
) -> KepubConversion:
    if not settings.enable_kepubify:
        raise KepubConversionError("KEPUB conversion is disabled")
    if settings.kepubify_path is None:
        raise KepubConversionError("KEPUBIFY_PATH is not configured")
    if not source_format.path.is_file():
        raise KepubConversionError("source EPUB is missing")

    cache_path = kepub_cache_path(book, source_format, settings)
    lock = _conversion_lock(book.uuid)
    with lock:
        if cache_path.is_file():
            return KepubConversion(path=cache_path, created=False)

        cache_path.parent.mkdir(parents=True, exist_ok=True)
        with TemporaryDirectory(dir=settings.companion_cache_path) as temp_directory:
            temp_dir = Path(temp_directory)
            output_dir = temp_dir / "out"
            output_dir.mkdir()
            command = [
                str(settings.kepubify_path),
                "-o",
                str(output_dir),
                str(source_format.path),
            ]
            try:
                subprocess.run(
                    command,
                    check=True,
                    capture_output=True,
                    timeout=settings.kepub_conversion_timeout_seconds,
                )
            except (OSError, subprocess.CalledProcessError, subprocess.TimeoutExpired) as exc:
                raise KepubConversionError("kepubify conversion failed") from exc

            converted_path = _find_converted_kepub(output_dir)
            cache_temp_path = temp_dir / cache_path.name
            shutil.move(str(converted_path), cache_temp_path)
            cache_temp_path.replace(cache_path)

        return KepubConversion(path=cache_path, created=True)


def kepub_cache_path(
    book: CalibreBook,
    source_format: CalibreFormat,
    settings: Settings,
) -> Path:
    source_stat = source_format.path.stat()
    source_mtime = source_stat.st_mtime_ns
    source_size = source_stat.st_size
    cache_name = f"{source_format.format.lower()}-{source_mtime}-{source_size}.kepub.epub"
    return settings.companion_cache_path / "kepub" / book.uuid / cache_name


def _conversion_lock(book_uuid: str) -> Lock:
    with _locks_guard:
        lock = _conversion_locks.get(book_uuid)
        if lock is None:
            lock = Lock()
            _conversion_locks[book_uuid] = lock
        return lock


def _find_converted_kepub(output_dir: Path) -> Path:
    converted = sorted(
        path
        for path in output_dir.rglob("*")
        if path.is_file() and path.name.lower().endswith((".kepub.epub", ".kepub"))
    )
    if not converted:
        converted = sorted(path for path in output_dir.rglob("*") if path.is_file())
    if len(converted) != 1:
        raise KepubConversionError("kepubify did not produce one output file")
    return converted[0]
