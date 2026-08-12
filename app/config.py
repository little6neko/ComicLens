from __future__ import annotations

import os
from collections.abc import Mapping
from dataclasses import dataclass, field
from pathlib import Path

PROJECT_ROOT = Path(__file__).resolve().parents[1]


def _read_int(name: str, default: int, *, minimum: int = 1) -> int:
    raw_value = os.getenv(name, str(default)).strip()
    try:
        value = int(raw_value)
    except ValueError as exc:
        raise RuntimeError(f"{name} must be an integer") from exc
    if value < minimum:
        raise RuntimeError(f"{name} must be greater than or equal to {minimum}")
    return value


def _read_float(name: str, default: float, *, minimum: float = 0.1) -> float:
    raw_value = os.getenv(name, str(default)).strip()
    try:
        value = float(raw_value)
    except ValueError as exc:
        raise RuntimeError(f"{name} must be a number") from exc
    if value < minimum:
        raise RuntimeError(f"{name} must be greater than or equal to {minimum}")
    return value


@dataclass(frozen=True, slots=True)
class AppConfig:
    app_name: str
    host: str
    port: int
    data_dir: Path
    static_dir: Path
    cache_max_mb: int
    access_password: str | None
    upstream_base_url: str
    request_timeout: float
    log_level: str
    initial_settings: Mapping[str, object] = field(default_factory=dict)

    @property
    def database_path(self) -> Path:
        return self.data_dir / "comiclens.db"

    @property
    def cache_dir(self) -> Path:
        return self.data_dir / "cache"

    @property
    def secrets_path(self) -> Path:
        return self.data_dir / "secrets.key"

    @classmethod
    def from_env(
        cls,
        *,
        data_dir: Path | None = None,
        static_dir: Path | None = None,
    ) -> AppConfig:
        configured_data_dir = data_dir or Path(
            os.getenv("COMICLENS_DATA_DIR", PROJECT_ROOT / "data")
        )
        configured_static_dir = static_dir or Path(
            os.getenv("COMICLENS_STATIC_DIR", PROJECT_ROOT / "web" / "dist")
        )
        access_password = os.getenv("COMICLENS_ACCESS_PASSWORD", "")
        initial_settings = {
            key: value
            for key, value in {
                "ocr_api_url": os.getenv("COMICLENS_OCR_API_URL"),
                "ocr_token": os.getenv("COMICLENS_OCR_TOKEN"),
                "ocr_basic_username": os.getenv("COMICLENS_OCR_BASIC_USERNAME"),
                "ocr_basic_password": os.getenv("COMICLENS_OCR_BASIC_PASSWORD"),
                "ocr_model": os.getenv("COMICLENS_OCR_MODEL"),
                "deeplx_url": os.getenv("COMICLENS_DEEPLX_URL"),
                "fallback_proxy_url": os.getenv("COMICLENS_PROXY_URL"),
            }.items()
            if value not in {None, ""}
        }
        return cls(
            app_name="ComicLens",
            host=os.getenv("COMICLENS_HOST", "0.0.0.0").strip() or "0.0.0.0",
            port=_read_int("PORT", 8233),
            data_dir=configured_data_dir.expanduser().resolve(),
            static_dir=configured_static_dir.expanduser().resolve(),
            cache_max_mb=_read_int("COMICLENS_CACHE_MAX_MB", 5120),
            access_password=access_password if access_password else None,
            upstream_base_url=os.getenv(
                "COMICLENS_UPSTREAM_BASE_URL", "https://manga18fx.com"
            ).rstrip("/"),
            request_timeout=_read_float("COMICLENS_REQUEST_TIMEOUT", 30.0),
            log_level=os.getenv("COMICLENS_LOG_LEVEL", "INFO").strip().upper() or "INFO",
            initial_settings=initial_settings,
        )

    def ensure_directories(self) -> None:
        self.data_dir.mkdir(parents=True, exist_ok=True)
        self.cache_dir.mkdir(parents=True, exist_ok=True)
