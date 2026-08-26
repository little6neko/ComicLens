from __future__ import annotations

import json
import sqlite3
from pathlib import Path

import pytest
from fastapi.testclient import TestClient

from app.application.settings import SettingsService
from app.config import AppConfig
from app.domain.settings import ServerSettingsPatch
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
        generation_columns = {
            str(row["name"])
            for row in database.fetchall("PRAGMA table_info(translation_generations)")
        }
        batch_indexes = {
            str(row["name"]) for row in database.fetchall("PRAGMA index_list(translation_batches)")
        }
    finally:
        database.close()

    assert {row["version"] for row in versions} == {1, 2, 3, 4, 5, 6, 7, 8, 9}
    assert {
        "app_settings",
        "favorites",
        "reading_history",
        "read_chapters",
        "translation_generations",
        "translation_pages",
        "active_translation_pages",
        "translation_segments",
        "active_translation_segments",
        "translation_batches",
        "translation_batch_items",
        "cache_bundles",
        "cache_entries",
        "media_sources",
        "app_metadata",
    }.issubset(tables)
    assert "batch_item_id" in generation_columns
    assert "translation_batches_open_comic_idx" in batch_indexes
    assert journal_mode == "wal"
    assert foreign_keys == 1


def test_pretranslation_migration_preserves_existing_database(tmp_path: Path) -> None:
    database_path = tmp_path / "legacy.db"
    migrations_dir = Path(__file__).parents[1] / "app" / "repositories" / "migrations"
    connection = sqlite3.connect(database_path)
    try:
        connection.execute(
            """
            CREATE TABLE schema_migrations (
                version INTEGER PRIMARY KEY,
                name TEXT NOT NULL,
                applied_at INTEGER NOT NULL
            )
            """
        )
        for migration_path in sorted(migrations_dir.glob("00[1-8]_*.sql")):
            version = int(migration_path.name.split("_", 1)[0])
            connection.executescript(migration_path.read_text(encoding="utf-8"))
            connection.execute(
                "INSERT INTO schema_migrations(version, name, applied_at) VALUES (?, ?, 1)",
                (version, migration_path.name),
            )
        connection.execute(
            """
            INSERT INTO app_settings(key, value, is_secret, updated_at)
            VALUES ('target_language', '"ZH-HANS"', 0, 1)
            """
        )
        connection.execute(
            """
            INSERT INTO translation_generations(
                generation_id, comic_id, chapter_id,
                semantic_fingerprint, semantic_settings_json,
                status, stop_requested, total_pages, completed_pages,
                failed_pages, created_at, updated_at, kind,
                planning_complete, total_segments, completed_segments,
                failed_segments
            ) VALUES (
                'legacy-generation', 'alpha', 'chapter-1',
                'fingerprint', '{}', 'completed', 0, 0, 0, 0,
                1, 1, 'normal', 1, 0, 0, 0
            )
            """
        )
        connection.commit()
    finally:
        connection.close()

    migrated = Database(database_path)
    try:
        generation = migrated.fetchone(
            "SELECT * FROM translation_generations WHERE generation_id = 'legacy-generation'"
        )
        setting = migrated.fetchone(
            "SELECT value FROM app_settings WHERE key = 'target_language'"
        )
        versions = {
            int(row["version"])
            for row in migrated.fetchall("SELECT version FROM schema_migrations")
        }
    finally:
        migrated.close()

    assert generation is not None
    assert generation["status"] == "completed"
    assert generation["batch_item_id"] is None
    assert setting is not None and setting["value"] == '"ZH-HANS"'
    assert versions == {1, 2, 3, 4, 5, 6, 7, 8, 9}


def test_new_settings_use_auto_ocr_without_auth_and_sync_example_url(tmp_path: Path) -> None:
    config = config_for(tmp_path)
    with TestClient(create_app(config)) as client:
        settings = client.get("/api/settings")

    assert settings.status_code == 200
    payload = settings.json()
    assert "theme" not in payload
    assert "readingMode" not in payload
    assert payload["sourceLanguage"] == "AUTO"
    assert payload["targetLanguage"] == "ZH-HANS"
    assert payload["ocrMode"] == "auto"
    assert payload["ocrAuthMode"] == "none"
    assert payload["ocrApiUrl"] == "http://example.com/layout-parsing"
    assert payload["ocrBasicUsername"] == ""
    assert payload["ocrBasicPassword"] == {"configured": False, "masked": None}
    assert payload["ocrModel"] == "PaddleOCR-VL-1.6"
    assert payload["ocrPollIntervalSeconds"] == 2.0
    assert payload["ocrTimeoutSeconds"] == 180.0
    assert payload["ocrConcurrency"] == 2
    assert "realtimeTranslationDefault" not in payload
    assert payload["ocrSliceHeight"] == 1600
    assert payload["ocrSliceOverlap"] == 200
    assert payload["translationService"] == "deepl"
    assert payload["deeplApiKey"] == {"configured": False, "masked": None}
    assert payload["translationTimeoutSeconds"] == 30.0
    assert payload["proxyUrl"] == ""
    assert payload["proxyUsername"] == ""
    assert payload["proxyPassword"] == {"configured": False, "masked": None}
    assert "fallbackProxyUrl" not in payload
    assert "deeplxTimeoutSeconds" not in payload

    database = Database(config.database_path)
    cipher = SecretCipher(config.secrets_path, database)
    values = SettingsService(database, cipher, config).values(include_secrets=True)
    database.close()

    assert values["ocr_api_url"] == "http://example.com/layout-parsing"


def test_new_url_and_proxy_credential_storage_matches_visibility_rules(
    tmp_path: Path,
) -> None:
    ocr_url = "https://ocr.example/layout-parsing?tenant=visible"
    proxy_url = "http://embedded:visible@proxy.example:8080"
    config = config_for(
        tmp_path,
        initial_settings={
            "ocr_api_url": ocr_url,
            "proxy_url": proxy_url,
            "proxy_username": "proxy-user",
            "proxy_password": "proxy-secret-password",
        },
    )

    with TestClient(create_app(config)) as client:
        response = client.get("/api/settings")

    database = Database(config.database_path)
    cipher = SecretCipher(config.secrets_path, database)
    rows = {
        str(row["key"]): row
        for row in database.fetchall(
            "SELECT key, value, is_secret FROM app_settings ORDER BY key"
        )
    }
    database.close()

    assert response.status_code == 200
    assert response.json()["ocrApiUrl"] == ocr_url
    assert response.json()["proxyUrl"] == proxy_url
    assert response.json()["proxyUsername"] == "proxy-user"
    assert response.json()["proxyPassword"] == {
        "configured": True,
        "masked": "••••word",
    }
    assert rows["ocr_api_url"]["is_secret"] == 0
    assert json.loads(str(rows["ocr_api_url"]["value"])) == ocr_url
    assert rows["proxy_url"]["is_secret"] == 0
    assert json.loads(str(rows["proxy_url"]["value"])) == proxy_url
    assert rows["proxy_username"]["is_secret"] == 0
    assert json.loads(str(rows["proxy_username"]["value"])) == "proxy-user"
    assert rows["proxy_password"]["is_secret"] == 1
    assert json.loads(cipher.decrypt(str(rows["proxy_password"]["value"]))) == (
        "proxy-secret-password"
    )


def test_saved_ocr_concurrency_updates_running_manager_immediately(tmp_path: Path) -> None:
    with TestClient(create_app(config_for(tmp_path))) as client:
        manager = client.app.state.translation_manager
        assert manager.ocr_concurrency == 2

        updated = client.patch("/api/settings", json={"ocrConcurrency": 3})
        assert updated.status_code == 200
        assert updated.json()["ocrConcurrency"] == 3
        assert manager.ocr_concurrency == 3

        rejected = client.patch("/api/settings", json={"ocrConcurrency": 0})
        assert rejected.status_code == 422
        assert manager.ocr_concurrency == 3
        assert client.get("/api/settings").json()["ocrConcurrency"] == 3


def test_saved_comic_proxy_updates_running_source_provider_immediately(tmp_path: Path) -> None:
    with TestClient(create_app(config_for(tmp_path))) as client:
        source = client.app.state.comic_source
        assert source._proxy_url() == ""

        updated = client.patch(
            "/api/settings",
            json={"proxyUrl": "http://embedded:old@proxy.example:8080"},
        )
        assert updated.status_code == 200
        assert source._proxy_url() == "http://embedded:old@proxy.example:8080"

        username_updated = client.patch(
            "/api/settings",
            json={"proxyUsername": "new user"},
        )
        assert username_updated.status_code == 200
        assert source._proxy_url() == "http://new%20user@proxy.example:8080"

        password_updated = client.patch(
            "/api/settings",
            json={
                "proxyPassword": {
                    "action": "replace",
                    "value": "new@password",
                }
            },
        )
        assert password_updated.status_code == 200
        assert password_updated.json()["proxyPassword"] == {
            "configured": True,
            "masked": "••••word",
        }
        assert source._proxy_url() == (
            "http://new%20user:new%40password@proxy.example:8080"
        )

        username_cleared = client.patch(
            "/api/settings",
            json={"proxyUsername": ""},
        )
        assert username_cleared.status_code == 200
        assert source._proxy_url() == "http://:new%40password@proxy.example:8080"

        password_cleared = client.patch(
            "/api/settings",
            json={"proxyPassword": {"action": "clear"}},
        )
        assert password_cleared.status_code == 200
        assert source._proxy_url() == "http://embedded:old@proxy.example:8080"

        cleared = client.patch(
            "/api/settings",
            json={"proxyUrl": ""},
        )
        assert cleared.status_code == 200
        assert source._proxy_url() == ""


def test_browser_preferences_are_removed_from_upgraded_database(tmp_path: Path) -> None:
    database_path = tmp_path / "comiclens.db"
    database = Database(database_path)
    database.execute(
        "INSERT INTO app_settings(key, value, is_secret, updated_at) VALUES (?, ?, 0, 1)",
        ("theme", '"dark"'),
    )
    database.execute(
        "INSERT INTO app_settings(key, value, is_secret, updated_at) VALUES (?, ?, 0, 1)",
        ("reading_mode", '"double"'),
    )
    database.execute("DELETE FROM schema_migrations WHERE version = 8")
    database.close()

    migrated = Database(database_path)
    try:
        stored_keys = {
            str(row["key"])
            for row in migrated.fetchall("SELECT key FROM app_settings ORDER BY key")
        }
    finally:
        migrated.close()

    assert "theme" not in stored_keys
    assert "reading_mode" not in stored_keys


@pytest.mark.parametrize(
    "payload",
    [
        {"theme": "light"},
        {"readingMode": "page"},
        {"realtimeTranslationDefault": True},
    ],
)
def test_settings_reject_removed_browser_preferences(
    tmp_path: Path,
    payload: dict[str, str],
) -> None:
    with TestClient(create_app(config_for(tmp_path))) as client:
        response = client.patch("/api/settings", json=payload)

    assert response.status_code == 422
    assert response.json()["code"] == "VALIDATION_ERROR"


def test_v5_settings_drop_realtime_translation_default_and_preserve_other_values(
    tmp_path: Path,
) -> None:
    config = config_for(tmp_path)
    database = Database(config.database_path)
    cipher = SecretCipher(config.secrets_path, database)
    settings = SettingsService(database, cipher, config)
    settings.patch(
        ServerSettingsPatch(
            page_direction="rtl",
            ocr_concurrency=1,
            ocr_token={"action": "replace", "value": "preserved-secret"},
        )
    )
    database.execute(
        """
        INSERT INTO app_settings(key, value, is_secret, updated_at)
        VALUES ('realtime_translation_default', 'true', 0, 1)
        """
    )
    database.execute(
        "UPDATE app_metadata SET value = '5' WHERE key = ?",
        ("settings_schema_version",),
    )

    migrated = SettingsService(database, cipher, config).values(include_secrets=True)
    stored_keys = {
        str(row["key"]) for row in database.fetchall("SELECT key FROM app_settings ORDER BY key")
    }
    schema_version = database.scalar(
        "SELECT value FROM app_metadata WHERE key = ?",
        ("settings_schema_version",),
    )
    database.close()

    assert "realtime_translation_default" not in migrated
    assert "realtime_translation_default" not in stored_keys
    assert migrated["page_direction"] == "rtl"
    assert migrated["ocr_concurrency"] == 1
    assert migrated["ocr_token"] == "preserved-secret"
    assert schema_version == "7"


def test_v2_settings_migrate_only_the_old_default_slice_height(tmp_path: Path) -> None:
    config = config_for(tmp_path)
    database = Database(config.database_path)
    cipher = SecretCipher(config.secrets_path, database)
    settings = SettingsService(database, cipher, config)
    settings.patch(ServerSettingsPatch(source_language="EN", ocr_slice_height=4000))
    database.execute(
        "UPDATE app_metadata SET value = '2' WHERE key = ?",
        ("settings_schema_version",),
    )

    migrated = SettingsService(database, cipher, config).values(include_secrets=True)
    assert migrated["ocr_slice_height"] == 1600
    assert migrated["ocr_slice_overlap"] == 200
    assert migrated["source_language"] == "EN"

    database.execute(
        "UPDATE app_settings SET value = '2400' WHERE key = 'ocr_slice_height'"
    )
    database.execute(
        "UPDATE app_metadata SET value = '2' WHERE key = ?",
        ("settings_schema_version",),
    )
    preserved = SettingsService(database, cipher, config).values(include_secrets=True)
    database.close()

    assert preserved["ocr_slice_height"] == 2400


def test_legacy_settings_migrate_once_and_preserve_ocr_auth_and_deeplx(
    tmp_path: Path,
) -> None:
    config = config_for(tmp_path)
    database = Database(config.database_path)
    cipher = SecretCipher(config.secrets_path, database)
    _insert_legacy_settings(
        database,
        cipher,
        {
            "source_language": ("EN", False),
            "ocr_mode": ("auto", False),
            "ocr_auth_mode": ("bearer", False),
            "ocr_api_url": ("", True),
            "ocr_token": ("ocr-secret", True),
            "ocr_basic_username": ("legacy-user", False),
            "ocr_basic_password": ("legacy-password", True),
            "ocr_model": ("PaddleOCR-VL-1.5", False),
            "ocr_poll_interval_seconds": (2.0, False),
            "ocr_timeout_seconds": (180.0, False),
            "ocr_concurrency": (1, False),
            "deeplx_url": ("https://deeplx.example/translate", True),
            "deeplx_timeout_seconds": (47.0, False),
            "translation_concurrency": (3, False),
        },
    )

    settings = SettingsService(database, cipher, config)
    migrated = settings.values(include_secrets=True)
    stored_keys = {
        str(row["key"]) for row in database.fetchall("SELECT key FROM app_settings ORDER BY key")
    }

    assert migrated["source_language"] == "AUTO"
    assert migrated["ocr_mode"] == "auto"
    assert migrated["ocr_auth_mode"] == "bearer"
    assert migrated["ocr_api_url"] == "http://example.com/layout-parsing"
    assert migrated["ocr_token"] == "ocr-secret"
    assert migrated["ocr_basic_username"] == "legacy-user"
    assert migrated["ocr_basic_password"] == "legacy-password"
    assert migrated["ocr_model"] == "PaddleOCR-VL-1.6"
    assert migrated["translation_service"] == "deeplx"
    assert migrated["deeplx_url"] == "https://deeplx.example/translate"
    assert migrated["translation_timeout_seconds"] == 47.0
    assert migrated["translation_concurrency"] == 3
    assert "ocr_mode" in stored_keys
    assert "ocr_auth_mode" in stored_keys
    assert "ocr_basic_username" in stored_keys
    assert "ocr_basic_password" in stored_keys
    assert "deeplx_timeout_seconds" not in stored_keys

    settings.patch(
        ServerSettingsPatch(
            source_language="EN",
            ocr_auth_mode="none",
            ocr_model="custom-ocr-model",
            translation_service="deepl",
        )
    )
    restarted = SettingsService(database, cipher, config).values(include_secrets=True)
    database.close()

    assert restarted["source_language"] == "EN"
    assert restarted["ocr_auth_mode"] == "none"
    assert restarted["ocr_model"] == "custom-ocr-model"
    assert restarted["translation_service"] == "deepl"


def test_legacy_custom_ocr_and_korean_without_deeplx_are_preserved(tmp_path: Path) -> None:
    config = config_for(tmp_path)
    database = Database(config.database_path)
    cipher = SecretCipher(config.secrets_path, database)
    _insert_legacy_settings(
        database,
        cipher,
        {
            "source_language": ("KO", False),
            "ocr_mode": ("direct", False),
            "ocr_auth_mode": ("basic", False),
            "ocr_api_url": ("https://ocr.example/custom/jobs", True),
            "ocr_basic_username": ("comic-user", False),
            "ocr_basic_password": ("comic-password", True),
            "ocr_model": ("custom-model", False),
            "deeplx_url": ("", True),
        },
    )

    migrated = SettingsService(database, cipher, config).values(include_secrets=True)
    database.close()

    assert migrated["source_language"] == "KO"
    assert migrated["ocr_mode"] == "direct"
    assert migrated["ocr_auth_mode"] == "basic"
    assert migrated["ocr_api_url"] == "https://ocr.example/custom/jobs"
    assert migrated["ocr_basic_username"] == "comic-user"
    assert migrated["ocr_basic_password"] == "comic-password"
    assert migrated["ocr_model"] == "custom-model"
    assert migrated["translation_service"] == "deepl"


@pytest.mark.parametrize(
    ("token", "expected_auth_mode"),
    [("legacy-token", "bearer"), ("", "none")],
)
def test_v3_settings_restore_auto_mode_and_infer_auth_from_token(
    tmp_path: Path,
    token: str,
    expected_auth_mode: str,
) -> None:
    config = config_for(tmp_path, initial_settings={"ocr_token": token} if token else {})
    database = Database(config.database_path)
    cipher = SecretCipher(config.secrets_path, database)
    SettingsService(database, cipher, config)
    database.execute(
        "DELETE FROM app_settings WHERE key IN (?, ?, ?, ?)",
        ("ocr_mode", "ocr_auth_mode", "ocr_basic_username", "ocr_basic_password"),
    )
    database.execute(
        "UPDATE app_metadata SET value = '3' WHERE key = ?",
        ("settings_schema_version",),
    )

    migrated = SettingsService(database, cipher, config).values(include_secrets=True)
    database.close()

    assert migrated["ocr_mode"] == "auto"
    assert migrated["ocr_auth_mode"] == expected_auth_mode
    assert migrated["ocr_token"] == token
    assert migrated["ocr_basic_username"] == ""
    assert migrated["ocr_basic_password"] == ""


@pytest.mark.parametrize(
    ("initial_proxy_url", "expected_proxy_url"),
    [(None, ""), ("http://seed-proxy.example:8080", "http://seed-proxy.example:8080")],
)
def test_v4_settings_drop_fallback_proxy_without_copying_its_value(
    tmp_path: Path,
    initial_proxy_url: str | None,
    expected_proxy_url: str,
) -> None:
    initial_settings = {"proxy_url": initial_proxy_url} if initial_proxy_url else {}
    config = config_for(tmp_path, initial_settings=initial_settings)
    database = Database(config.database_path)
    cipher = SecretCipher(config.secrets_path, database)
    _insert_legacy_settings(
        database,
        cipher,
        {
            "page_direction": ("rtl", False),
            "fallback_proxy_url": ("http://old-user:old-password@old-proxy.example:8080", True),
        },
    )
    database.execute(
        """
        INSERT INTO app_metadata(key, value, updated_at)
        VALUES (?, '4', 1)
        """,
        ("settings_schema_version",),
    )

    migrated = SettingsService(database, cipher, config).values(include_secrets=True)
    stored_keys = {
        str(row["key"]) for row in database.fetchall("SELECT key FROM app_settings ORDER BY key")
    }
    schema_version = database.scalar(
        "SELECT value FROM app_metadata WHERE key = ?",
        ("settings_schema_version",),
    )
    database.close()

    assert migrated["page_direction"] == "rtl"
    assert migrated["proxy_url"] == expected_proxy_url
    assert "fallback_proxy_url" not in stored_keys
    assert "proxy_url" in stored_keys
    assert schema_version == "7"


def test_v6_settings_convert_urls_to_plaintext_without_rewriting_values(
    tmp_path: Path,
) -> None:
    config = config_for(tmp_path, initial_settings={"ocr_token": "preserved-token"})
    database = Database(config.database_path)
    cipher = SecretCipher(config.secrets_path, database)
    SettingsService(database, cipher, config)
    ocr_url = " https://ocr.example/layout-parsing?key=visible%20value "
    proxy_url = "http://embedded:user%40pass@proxy.example:8080"
    for key, value in (("ocr_api_url", ocr_url), ("proxy_url", proxy_url)):
        serialized = json.dumps(value, ensure_ascii=False, separators=(",", ":"))
        database.execute(
            "UPDATE app_settings SET value = ?, is_secret = 1 WHERE key = ?",
            (cipher.encrypt(serialized), key),
        )
    database.execute(
        "DELETE FROM app_settings WHERE key IN ('proxy_username', 'proxy_password')"
    )
    database.execute(
        "UPDATE app_metadata SET value = '6' WHERE key = ?",
        ("settings_schema_version",),
    )

    migrated = SettingsService(database, cipher, config).values(include_secrets=True)
    rows = {
        str(row["key"]): row
        for row in database.fetchall(
            "SELECT key, value, is_secret FROM app_settings ORDER BY key"
        )
    }
    schema_version = database.scalar(
        "SELECT value FROM app_metadata WHERE key = ?",
        ("settings_schema_version",),
    )
    database.close()

    assert migrated["ocr_api_url"] == ocr_url
    assert migrated["proxy_url"] == proxy_url
    assert migrated["proxy_username"] == ""
    assert migrated["proxy_password"] == ""
    assert migrated["ocr_token"] == "preserved-token"
    assert rows["ocr_api_url"]["is_secret"] == 0
    assert json.loads(str(rows["ocr_api_url"]["value"])) == ocr_url
    assert rows["proxy_url"]["is_secret"] == 0
    assert json.loads(str(rows["proxy_url"]["value"])) == proxy_url
    assert rows["proxy_username"]["is_secret"] == 0
    assert rows["proxy_password"]["is_secret"] == 1
    assert rows["ocr_token"]["is_secret"] == 1
    assert schema_version == "7"


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
                "ocrToken": {"action": "replace", "value": "new-secret-token"},
                "ocrApiUrl": "https://ocr.example/layout-parsing?key=visible-value",
                "ocrAuthMode": "basic",
                "ocrBasicUsername": "basic-user",
                "ocrBasicPassword": {
                    "action": "replace",
                    "value": "basic-secret-password",
                },
                "deeplApiKey": {"action": "replace", "value": "test-deepl-key:fx"},
                "deeplxUrl": {
                    "action": "replace",
                    "value": "https://translator.example/api?key=private",
                },
                "proxyUrl": "http://url-user:url-password@proxy.example:8080",
                "proxyUsername": "proxy-user",
                "proxyPassword": {
                    "action": "replace",
                    "value": "proxy-secret-password",
                },
            },
        )
        kept = client.patch(
            "/api/settings",
            json={
                "ocrToken": {"action": "keep"},
                "ocrBasicPassword": {"action": "keep"},
                "proxyPassword": {"action": "keep"},
            },
        )

    database_bytes = config.database_path.read_bytes()
    assert initial.status_code == 200
    assert initial.json()["ocrToken"] == {
        "configured": True,
        "masked": "••••cret",
    }
    assert updated.status_code == 200
    assert updated.json()["ocrToken"] == {
        "configured": True,
        "masked": "••••oken",
    }
    assert updated.json()["ocrAuthMode"] == "basic"
    assert updated.json()["ocrBasicUsername"] == "basic-user"
    assert updated.json()["ocrBasicPassword"] == {
        "configured": True,
        "masked": "••••word",
    }
    assert updated.json()["deeplApiKey"] == {
        "configured": True,
        "masked": "••••y:fx",
    }
    assert updated.json()["ocrApiUrl"] == (
        "https://ocr.example/layout-parsing?key=visible-value"
    )
    assert updated.json()["proxyUrl"] == (
        "http://url-user:url-password@proxy.example:8080"
    )
    assert updated.json()["proxyUsername"] == "proxy-user"
    assert updated.json()["proxyPassword"] == {
        "configured": True,
        "masked": "••••word",
    }
    assert kept.json()["ocrToken"]["configured"] is True
    assert kept.json()["ocrBasicPassword"]["configured"] is True
    assert kept.json()["proxyPassword"]["configured"] is True
    assert b"new-secret-token" not in database_bytes
    assert b"basic-secret-password" not in database_bytes
    assert b"test-deepl-key:fx" not in database_bytes
    assert b"translator.example" not in database_bytes
    assert b"proxy-secret-password" not in database_bytes
    assert b"ocr.example" in database_bytes
    assert b"visible-value" in database_bytes
    assert b"url-password" in database_bytes
    assert b"proxy.example" in database_bytes
    assert b"proxy-user" in database_bytes

    # A later environment seed does not overwrite persisted settings.
    restarted_config = config_for(
        tmp_path,
        initial_settings={"ocr_token": "ignored-seed", "ocr_model": "ignored-model"},
    )
    with TestClient(create_app(restarted_config)) as client:
        persisted = client.get("/api/settings")
        cleared = client.patch(
            "/api/settings",
            json={
                "ocrToken": {"action": "clear"},
                "ocrBasicPassword": {"action": "clear"},
                "proxyUrl": "",
                "proxyUsername": "",
                "proxyPassword": {"action": "clear"},
            },
        )

    assert persisted.json()["ocrModel"] == "seed-model"
    assert persisted.json()["ocrToken"]["masked"] == "••••oken"
    assert cleared.json()["ocrToken"] == {"configured": False, "masked": None}
    assert cleared.json()["ocrBasicPassword"] == {"configured": False, "masked": None}
    assert cleared.json()["proxyUrl"] == ""
    assert cleared.json()["proxyUsername"] == ""
    assert cleared.json()["proxyPassword"] == {"configured": False, "masked": None}


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


def _insert_legacy_settings(
    database: Database,
    cipher: SecretCipher,
    settings: dict[str, tuple[object, bool]],
) -> None:
    for key, (value, is_secret) in settings.items():
        serialized = json.dumps(value, ensure_ascii=False, separators=(",", ":"))
        stored = cipher.encrypt(serialized) if is_secret else serialized
        database.execute(
            """
            INSERT INTO app_settings(key, value, is_secret, updated_at)
            VALUES (?, ?, ?, 1)
            """,
            (key, stored, int(is_secret)),
        )
