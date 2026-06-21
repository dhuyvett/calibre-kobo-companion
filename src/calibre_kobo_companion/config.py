from __future__ import annotations

from dataclasses import dataclass
import os
from pathlib import Path


DEFAULT_LISTEN_HOST = "0.0.0.0"
DEFAULT_LISTEN_PORT = 8080
DEFAULT_SYNC_PAGE_SIZE = 100
DEFAULT_KEPUB_CACHE_MAX_MB = 1024
DEFAULT_KEPUB_CONVERSION_TIMEOUT_SECONDS = 120
DEFAULT_KOBO_STORE_API_URL = "https://storeapi.kobo.com"
DEFAULT_KOBO_PROXY_TIMEOUT_SECONDS = 20


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
    kobo_sync_mode: str = "local"
    kobo_store_api_url: str = DEFAULT_KOBO_STORE_API_URL
    kobo_proxy_timeout_seconds: int = DEFAULT_KOBO_PROXY_TIMEOUT_SECONDS
    hybrid_sync_require_local_library: bool = False
    log_level: str = "info"
    enable_kepubify: bool = False
    kepubify_path: Path | None = None
    kepub_cache_max_mb: int = DEFAULT_KEPUB_CACHE_MAX_MB
    kepub_conversion_timeout_seconds: int = DEFAULT_KEPUB_CONVERSION_TIMEOUT_SECONDS
    tls_cert_path: Path | None = None
    tls_key_path: Path | None = None

    @property
    def metadata_db_path(self) -> Path:
        return self.calibre_library_path / "metadata.db"

    @property
    def tls_enabled(self) -> bool:
        return self.tls_cert_path is not None and self.tls_key_path is not None


def load_settings() -> Settings:
    library = os.environ.get("CALIBRE_LIBRARY_PATH")
    if not library:
        raise ConfigError("CALIBRE_LIBRARY_PATH is required")

    companion_db = os.environ.get("COMPANION_DB_PATH", "./data/companion.db")
    companion_cache = os.environ.get("COMPANION_CACHE_PATH", "./data/cache")
    public_base_url = os.environ.get("PUBLIC_BASE_URL", "http://localhost:8080")
    kepubify_path = os.environ.get("KEPUBIFY_PATH")
    tls_cert_path = os.environ.get("TLS_CERT_PATH")
    tls_key_path = os.environ.get("TLS_KEY_PATH")

    settings = Settings(
        calibre_library_path=Path(library).expanduser(),
        companion_db_path=Path(companion_db).expanduser(),
        companion_cache_path=Path(companion_cache).expanduser(),
        public_base_url=public_base_url.rstrip("/"),
        listen_host=os.environ.get("LISTEN_HOST", DEFAULT_LISTEN_HOST),
        listen_port=_int_from_env("LISTEN_PORT", default=DEFAULT_LISTEN_PORT),
        kobo_sync_page_size=_int_from_env("KOBO_SYNC_PAGE_SIZE", default=DEFAULT_SYNC_PAGE_SIZE),
        kobo_sync_mode=os.environ.get("KOBO_SYNC_MODE", "local").strip().lower(),
        kobo_store_api_url=os.environ.get(
            "KOBO_STORE_API_URL",
            DEFAULT_KOBO_STORE_API_URL,
        ).rstrip("/"),
        kobo_proxy_timeout_seconds=_int_from_env(
            "KOBO_PROXY_TIMEOUT_SECONDS",
            default=DEFAULT_KOBO_PROXY_TIMEOUT_SECONDS,
        ),
        hybrid_sync_require_local_library=_bool_from_env(
            os.environ.get("HYBRID_SYNC_REQUIRE_LOCAL_LIBRARY"),
            default=False,
        ),
        log_level=os.environ.get("LOG_LEVEL", "info"),
        enable_kepubify=_bool_from_env(os.environ.get("ENABLE_KEPUBIFY"), default=False),
        kepubify_path=Path(kepubify_path).expanduser() if kepubify_path else None,
        kepub_cache_max_mb=_int_from_env("KEPUB_CACHE_MAX_MB", default=DEFAULT_KEPUB_CACHE_MAX_MB),
        kepub_conversion_timeout_seconds=_int_from_env(
            "KEPUB_CONVERSION_TIMEOUT_SECONDS",
            default=DEFAULT_KEPUB_CONVERSION_TIMEOUT_SECONDS,
        ),
        tls_cert_path=Path(tls_cert_path).expanduser() if tls_cert_path else None,
        tls_key_path=Path(tls_key_path).expanduser() if tls_key_path else None,
    )
    validate_settings(settings)
    return settings


def validate_settings(settings: Settings) -> None:
    if settings.listen_port < 1 or settings.listen_port > 65535:
        raise ConfigError("LISTEN_PORT must be between 1 and 65535")
    if settings.kobo_sync_page_size < 1:
        raise ConfigError("KOBO_SYNC_PAGE_SIZE must be greater than zero")
    if settings.kobo_sync_mode not in {"local", "hybrid"}:
        raise ConfigError("KOBO_SYNC_MODE must be local or hybrid")
    if settings.kobo_proxy_timeout_seconds < 1:
        raise ConfigError("KOBO_PROXY_TIMEOUT_SECONDS must be greater than zero")
    if settings.kepub_cache_max_mb < 0:
        raise ConfigError("KEPUB_CACHE_MAX_MB must not be negative")
    if settings.kepub_conversion_timeout_seconds < 1:
        raise ConfigError("KEPUB_CONVERSION_TIMEOUT_SECONDS must be greater than zero")
    if (settings.tls_cert_path is None) != (settings.tls_key_path is None):
        raise ConfigError("TLS_CERT_PATH and TLS_KEY_PATH must be configured together")
