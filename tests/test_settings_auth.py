from __future__ import annotations

import sqlite3
from pathlib import Path

import pytest
from fastapi.testclient import TestClient

from app.application.settings import SettingsService
from app.config import AppConfig
from app.main import create_app
from app.repositories.database import Database
from app.security.secrets import SecretCipher


def config_for(
    tmp_path: Path,
    *,
    password: str | None = None,
    initial_settings: dict[str, object] | None = None,
) -> AppConfig:
    return AppConfig(
        app_name="ComicLens",
        host="0.0.0.0",
        port=8233,
        data_dir=tmp_path / "data",
        static_dir=tmp_path / "static",
        cache_max_mb=5120,
        access_password=password,
        upstream_base_url="https://manga18fx.com",
        request_timeout=30,
        log_level="INFO",
        initial_settings=initial_settings or {},
    )


def test_database_runs_all_migrations_with_wal_and_foreign_keys(tmp_path: Path) -> None:
    database = Database(tmp_path / "comiclens.db")
    try:
        tables = {
            row["name"]
            for row in database.fetchall("SELECT name FROM sqlite_master WHERE type = 'table'")
        }
        versions = database.fetchall("SELECT version FROM schema_migrations ORDER BY version")
        journal_mode = database.scalar("PRAGMA journal_mode")
        foreign_keys = database.scalar("PRAGMA foreign_keys")
    finally:
        database.close()

    assert {row["version"] for row in versions} == {1, 2, 3, 4, 5}
    assert {
        "app_settings",
        "favorites",
        "reading_history",
        "read_chapters",
        "translation_generations",
        "translation_pages",
        "active_translation_pages",
        "cache_bundles",
        "cache_entries",
        "media_sources",
    }.issubset(tables)
    assert journal_mode == "wal"
    assert foreign_keys == 1


def test_settings_encrypt_mask_and_persist_sensitive_values(tmp_path: Path) -> None:
    config = config_for(
        tmp_path,
        initial_settings={"ocr_token": "seed-secret", "ocr_model": "seed-model"},
    )

    with TestClient(create_app(config)) as client:
        initial = client.get("/api/settings")
        updated = client.patch(
            "/api/settings",
            json={
                "theme": "dark",
                "ocrToken": {"action": "replace", "value": "new-secret-token"},
                "deeplxUrl": {
                    "action": "replace",
                    "value": "https://translator.example/api?key=private",
                },
            },
        )
        kept = client.patch("/api/settings", json={"ocrToken": {"action": "keep"}})

    database_bytes = config.database_path.read_bytes()
    assert initial.status_code == 200
    assert initial.json()["ocrToken"] == {
        "configured": True,
        "masked": "••••cret",
    }
    assert updated.status_code == 200
    assert updated.json()["theme"] == "dark"
    assert updated.json()["ocrToken"] == {
        "configured": True,
        "masked": "••••oken",
    }
    assert kept.json()["ocrToken"]["configured"] is True
    assert b"new-secret-token" not in database_bytes
    assert b"translator.example" not in database_bytes

    # A later environment seed does not overwrite persisted settings.
    restarted_config = config_for(
        tmp_path,
        initial_settings={"ocr_token": "ignored-seed", "ocr_model": "ignored-model"},
    )
    with TestClient(create_app(restarted_config)) as client:
        persisted = client.get("/api/settings")
        cleared = client.patch("/api/settings", json={"ocrToken": {"action": "clear"}})

    assert persisted.json()["theme"] == "dark"
    assert persisted.json()["ocrModel"] == "seed-model"
    assert persisted.json()["ocrToken"]["masked"] == "••••oken"
    assert cleared.json()["ocrToken"] == {"configured": False, "masked": None}


def test_settings_reject_invalid_secret_protocol_and_slice_geometry(
    tmp_path: Path,
) -> None:
    with TestClient(create_app(config_for(tmp_path))) as client:
        missing_value = client.patch("/api/settings", json={"ocrToken": {"action": "replace"}})
        ambiguous_value = client.patch(
            "/api/settings",
            json={"ocrToken": {"action": "keep", "value": "unexpected"}},
        )
        invalid_overlap = client.patch(
            "/api/settings",
            json={"ocrSliceHeight": 1000, "ocrSliceOverlap": 1000},
        )

    assert missing_value.status_code == 422
    assert ambiguous_value.status_code == 422
    assert invalid_overlap.status_code == 422
    assert invalid_overlap.json()["code"] == "VALIDATION_ERROR"


def test_missing_secret_key_refuses_to_open_encrypted_database(tmp_path: Path) -> None:
    config = config_for(tmp_path, initial_settings={"ocr_token": "important"})
    with TestClient(create_app(config)) as client:
        assert client.get("/api/settings").status_code == 200

    config.secrets_path.unlink()

    with (
        pytest.raises(RuntimeError, match="secrets.key is missing"),
        TestClient(create_app(config)),
    ):
        pass


def test_password_disabled_skips_login_gate_and_reports_warning(tmp_path: Path) -> None:
    with TestClient(create_app(config_for(tmp_path))) as client:
        auth_config = client.get("/api/auth/config")
        session = client.get("/api/auth/session")
        settings = client.get("/api/settings")

    assert auth_config.json() == {"enabled": False}
    assert session.json() == {"enabled": False, "authenticated": True}
    assert settings.status_code == 200
    assert settings.json()["publicListenerWarning"] is True


def test_password_gate_protects_business_and_media_apis(tmp_path: Path) -> None:
    with TestClient(create_app(config_for(tmp_path, password="correct horse"))) as client:
        blocked_settings = client.get("/api/settings")
        blocked_media = client.get("/api/media/covers/alpha-comic")
        client.cookies.set("comiclens_session", "%%%not.a.token%%%")
        malformed_cookie = client.get("/api/settings")
        client.cookies.delete("comiclens_session")
        wrong = client.post("/api/auth/login", json={"password": "wrong"})
        login = client.post("/api/auth/login", json={"password": "correct horse"})
        allowed_settings = client.get("/api/settings")
        logout = client.post("/api/auth/logout")
        blocked_again = client.get("/api/settings")

    assert blocked_settings.status_code == 401
    assert blocked_settings.json()["code"] == "AUTH_REQUIRED"
    assert blocked_media.status_code == 401
    assert malformed_cookie.status_code == 401
    assert wrong.status_code == 401
    assert wrong.json()["code"] == "INVALID_PASSWORD"
    assert login.status_code == 200
    cookie_header = login.headers["set-cookie"]
    assert "HttpOnly" in cookie_header
    assert "SameSite=lax" in cookie_header
    assert allowed_settings.status_code == 200
    assert allowed_settings.json()["accessPasswordEnabled"] is True
    assert allowed_settings.json()["publicListenerWarning"] is False
    assert logout.json() == {"enabled": True, "authenticated": False}
    assert blocked_again.status_code == 401


def test_corrupt_encrypted_value_fails_loudly(tmp_path: Path) -> None:
    config = config_for(tmp_path)
    database = Database(config.database_path)
    cipher = SecretCipher(config.secrets_path, database)
    SettingsService(database, cipher, config)
    database.execute("UPDATE app_settings SET value = ? WHERE key = 'ocr_token'", ("corrupt",))
    database.close()

    with (
        pytest.raises(RuntimeError, match="restore the matching secrets.key"),
        TestClient(create_app(config)),
    ):
        pass


def test_database_schema_is_valid_sqlite(tmp_path: Path) -> None:
    database = Database(tmp_path / "schema.db")
    database.close()

    connection = sqlite3.connect(tmp_path / "schema.db")
    try:
        result = connection.execute("PRAGMA integrity_check").fetchone()
    finally:
        connection.close()

    assert result == ("ok",)
