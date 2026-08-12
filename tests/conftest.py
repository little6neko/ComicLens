from __future__ import annotations

from collections.abc import Iterator
from pathlib import Path

import pytest
from fastapi.testclient import TestClient

from app.config import AppConfig
from app.main import create_app


@pytest.fixture
def app_config(tmp_path: Path) -> AppConfig:
    return AppConfig(
        app_name="ComicLens",
        host="0.0.0.0",
        port=8233,
        data_dir=tmp_path / "data",
        static_dir=tmp_path / "static",
        cache_max_mb=5120,
        access_password=None,
        upstream_base_url="https://manga18fx.com",
        request_timeout=30.0,
        log_level="INFO",
    )


@pytest.fixture
def client(app_config: AppConfig) -> Iterator[TestClient]:
    with TestClient(create_app(app_config)) as test_client:
        yield test_client
