from __future__ import annotations

from dataclasses import dataclass
import os
from pathlib import Path


DEFAULT_LISTEN_HOST = "0.0.0.0"
DEFAULT_LISTEN_PORT = 8080
DEFAULT_SYNC_PAGE_SIZE = 100
DEFAULT_KEPUB_CACHE_MAX_MB = 1024
DEFAULT_KEPUB_CONVERSION_TIMEOUT_SECONDS = 120


def _bool_from_env(value: str | None, *, default: bool) -> bool:
    if value is None:
        return default
    return value.strip().lower() in {"1", "true", "yes", "on"}


def _int_from_env(name: str, *, default: int) -> int:
    value = os.environ.get(name)
    if value is None or value.strip() == "":
        return default
    try:
        return int(value)
    except ValueError as exc:
        raise ConfigError(f"{name} must be an integer") from exc


class ConfigError(ValueError):
    """Raised when runtime configuration is invalid."""


@dataclass(frozen=True)
class Settings:
    calibre_library_path: Path
    companion_db_path: Path
    companion_cache_path: Path
    public_base_url: str
    listen_host: str = DEFAULT_LISTEN_HOST
    listen_port: int = DEFAULT_LISTEN_PORT
    kobo_sync_page_size: int = DEFAULT_SYNC_PAGE_SIZE
    log_level: str = "info"
    allow_kobo_mutation_acks: bool = True
    enable_kepubify: bool = False
    kepubify_path: Path | None = None
    kepub_cache_max_mb: int = DEFAULT_KEPUB_CACHE_MAX_MB
    kepub_conversion_timeout_seconds: int = DEFAULT_KEPUB_CONVERSION_TIMEOUT_SECONDS

    @property
    def metadata_db_path(self) -> Path:
        return self.calibre_library_path / "metadata.db"


def load_settings() -> Settings:
    library = os.environ.get("CALIBRE_LIBRARY_PATH")
    if not library:
        raise ConfigError("CALIBRE_LIBRARY_PATH is required")

    companion_db = os.environ.get("COMPANION_DB_PATH", "./data/companion.db")
    companion_cache = os.environ.get("COMPANION_CACHE_PATH", "./data/cache")
    public_base_url = os.environ.get("PUBLIC_BASE_URL", "http://localhost:8080")
    kepubify_path = os.environ.get("KEPUBIFY_PATH")

    settings = Settings(
        calibre_library_path=Path(library).expanduser(),
        companion_db_path=Path(companion_db).expanduser(),
        companion_cache_path=Path(companion_cache).expanduser(),
        public_base_url=public_base_url.rstrip("/"),
        listen_host=os.environ.get("LISTEN_HOST", DEFAULT_LISTEN_HOST),
        listen_port=_int_from_env("LISTEN_PORT", default=DEFAULT_LISTEN_PORT),
        kobo_sync_page_size=_int_from_env("KOBO_SYNC_PAGE_SIZE", default=DEFAULT_SYNC_PAGE_SIZE),
        log_level=os.environ.get("LOG_LEVEL", "info"),
        allow_kobo_mutation_acks=_bool_from_env(
            os.environ.get("ALLOW_KOBO_MUTATION_ACKS"),
            default=True,
        ),
        enable_kepubify=_bool_from_env(os.environ.get("ENABLE_KEPUBIFY"), default=False),
        kepubify_path=Path(kepubify_path).expanduser() if kepubify_path else None,
        kepub_cache_max_mb=_int_from_env("KEPUB_CACHE_MAX_MB", default=DEFAULT_KEPUB_CACHE_MAX_MB),
        kepub_conversion_timeout_seconds=_int_from_env(
            "KEPUB_CONVERSION_TIMEOUT_SECONDS",
            default=DEFAULT_KEPUB_CONVERSION_TIMEOUT_SECONDS,
        ),
    )
    validate_settings(settings)
    return settings


def validate_settings(settings: Settings) -> None:
    if settings.listen_port < 1 or settings.listen_port > 65535:
        raise ConfigError("LISTEN_PORT must be between 1 and 65535")
    if settings.kobo_sync_page_size < 1:
        raise ConfigError("KOBO_SYNC_PAGE_SIZE must be greater than zero")
    if settings.kepub_cache_max_mb < 0:
        raise ConfigError("KEPUB_CACHE_MAX_MB must not be negative")
    if settings.kepub_conversion_timeout_seconds < 1:
        raise ConfigError("KEPUB_CONVERSION_TIMEOUT_SECONDS must be greater than zero")
