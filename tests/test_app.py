from __future__ import annotations

import logging
from pathlib import Path

from fastapi.testclient import TestClient

from app.config import AppConfig
from app.main import create_app
from app.observability import LOG_FORMAT


def test_health_endpoint(client: TestClient) -> None:
    response = client.get("/health")

    assert response.status_code == 200
    assert response.json() == {
        "status": "ok",
        "app": "ComicLens",
        "version": "0.1.4",
    }


def test_http_client_request_urls_are_not_logged_at_info() -> None:
    assert logging.getLogger("httpx").getEffectiveLevel() >= logging.WARNING
    assert logging.getLogger("httpcore").getEffectiveLevel() >= logging.WARNING


def test_application_log_format_omits_timestamp_and_logger_name() -> None:
    assert LOG_FORMAT == "%(levelname)s %(message)s"


def test_lifespan_creates_data_directories(client: TestClient, app_config: AppConfig) -> None:
    assert client.app.state.config is app_config
    assert app_config.data_dir.is_dir()
    assert app_config.cache_dir.is_dir()


def test_config_defaults_to_public_listener(monkeypatch, tmp_path: Path) -> None:
    monkeypatch.delenv("COMICLENS_HOST", raising=False)
    monkeypatch.delenv("COMICLENS_ACCESS_PASSWORD", raising=False)
    monkeypatch.setenv("PORT", "9123")

    config = AppConfig.from_env(data_dir=tmp_path / "data", static_dir=tmp_path / "web")

    assert config.host == "0.0.0.0"
    assert config.port == 9123
    assert config.access_password is None
    assert config.cache_max_mb == 5120


def test_invalid_integer_config_fails_fast(monkeypatch, tmp_path: Path) -> None:
    monkeypatch.setenv("PORT", "not-a-port")

    try:
        AppConfig.from_env(data_dir=tmp_path / "data", static_dir=tmp_path / "web")
    except RuntimeError as exc:
        assert str(exc) == "PORT must be an integer"
    else:
        raise AssertionError("invalid PORT should fail")


def test_config_seeds_deepl_key_and_basic_auth_env(
    monkeypatch,
    tmp_path: Path,
) -> None:
    monkeypatch.setenv("COMICLENS_DEEPL_API_KEY", "test-key:fx")
    monkeypatch.setenv("COMICLENS_OCR_BASIC_USERNAME", "seed-user")
    monkeypatch.setenv("COMICLENS_OCR_BASIC_PASSWORD", "seed-password")

    config = AppConfig.from_env(data_dir=tmp_path / "data", static_dir=tmp_path / "web")

    assert config.initial_settings["deepl_api_key"] == "test-key:fx"
    assert config.initial_settings["ocr_basic_username"] == "seed-user"
    assert config.initial_settings["ocr_basic_password"] == "seed-password"


def test_config_seeds_comic_proxy_from_existing_env_name(monkeypatch, tmp_path: Path) -> None:
    monkeypatch.setenv("COMICLENS_PROXY_URL", "http://comic-proxy.example:8080")

    config = AppConfig.from_env(data_dir=tmp_path / "data", static_dir=tmp_path / "web")

    assert config.initial_settings["proxy_url"] == "http://comic-proxy.example:8080"
    assert "fallback_proxy_url" not in config.initial_settings


def test_built_web_uses_spa_fallback_without_swallowing_api_errors(
    app_config: AppConfig,
) -> None:
    app_config.static_dir.mkdir(parents=True)
    app_config.static_dir.joinpath("index.html").write_text(
        "<!doctype html><title>ComicLens</title>", encoding="utf-8"
    )

    with TestClient(create_app(app_config)) as static_client:
        browser_route = static_client.get("/explore/category/action")
        missing_api = static_client.get("/api/does-not-exist")

    assert browser_route.status_code == 200
    assert "ComicLens" in browser_route.text
    assert missing_api.status_code == 404
    assert missing_api.json() == {
        "code": "NOT_FOUND",
        "message": "API 接口不存在",
        "retryable": False,
    }
