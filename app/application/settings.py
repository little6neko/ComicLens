from __future__ import annotations

import json
import time
from collections.abc import Mapping
from dataclasses import dataclass
from typing import Any

from app.config import AppConfig
from app.domain.settings import (
    SensitiveSettingPatch,
    SensitiveSettingState,
    ServerSettings,
    ServerSettingsPatch,
)
from app.repositories.database import Database
from app.security.secrets import SecretCipher

DEFAULT_OCR_API_URL = "http://example.com/layout-parsing"
DEFAULT_OCR_MODEL = "PaddleOCR-VL-1.6"
SETTINGS_SCHEMA_KEY = "settings_schema_version"
SETTINGS_SCHEMA_VERSION = 4


@dataclass(frozen=True, slots=True)
class SettingDefinition:
    default: object
    secret: bool = False


SETTING_DEFINITIONS: dict[str, SettingDefinition] = {
    "page_direction": SettingDefinition("ltr"),
    "realtime_translation_default": SettingDefinition(False),
    "source_language": SettingDefinition("AUTO"),
    "ocr_mode": SettingDefinition("auto"),
    "ocr_auth_mode": SettingDefinition("none"),
    "ocr_api_url": SettingDefinition(DEFAULT_OCR_API_URL, True),
    "ocr_token": SettingDefinition("", True),
    "ocr_basic_username": SettingDefinition(""),
    "ocr_basic_password": SettingDefinition("", True),
    "ocr_model": SettingDefinition(DEFAULT_OCR_MODEL),
    "ocr_poll_interval_seconds": SettingDefinition(2.0),
    "ocr_timeout_seconds": SettingDefinition(180.0),
    "ocr_concurrency": SettingDefinition(1),
    "translation_service": SettingDefinition("deepl"),
    "deepl_api_key": SettingDefinition("", True),
    "deeplx_url": SettingDefinition("", True),
    "translation_timeout_seconds": SettingDefinition(30.0),
    "translation_concurrency": SettingDefinition(2),
    "fallback_proxy_url": SettingDefinition("", True),
    "long_image_threshold": SettingDefinition(8000),
    "ocr_slice_height": SettingDefinition(1600),
    "ocr_slice_overlap": SettingDefinition(200),
    "reading_slice_height": SettingDefinition(3000),
    "cache_max_mb": SettingDefinition(5120),
}

SENSITIVE_SETTING_KEYS = {
    key for key, definition in SETTING_DEFINITIONS.items() if definition.secret
}


class SettingsService:
    def __init__(
        self,
        database: Database,
        cipher: SecretCipher,
        config: AppConfig,
    ) -> None:
        self.database = database
        self.cipher = cipher
        self.config = config
        self._initialize_or_migrate(config.initial_settings)
        # Fail during startup if the database and secrets.key do not belong together.
        self.values(include_secrets=True)

    def public_settings(self) -> ServerSettings:
        values = self.values(include_secrets=False)
        return ServerSettings(
            **values,
            target_language="ZH-HANS",
            access_password_enabled=self.config.access_password is not None,
            public_listener_warning=(
                self.config.host not in {"127.0.0.1", "::1", "localhost"}
                and self.config.access_password is None
            ),
        )

    def values(self, *, include_secrets: bool) -> dict[str, Any]:
        rows = self.database.fetchall("SELECT key, value, is_secret FROM app_settings")
        result: dict[str, Any] = {}
        for row in rows:
            key = str(row["key"])
            if key not in SETTING_DEFINITIONS:
                continue
            serialized = str(row["value"])
            is_secret = bool(row["is_secret"])
            if is_secret:
                plain_value = json.loads(self.cipher.decrypt(serialized))
                result[key] = (
                    plain_value if include_secrets else self._masked_state(str(plain_value))
                )
            else:
                result[key] = json.loads(serialized)
        return result

    def patch(self, patch: ServerSettingsPatch) -> ServerSettings:
        requested = patch.model_dump(exclude_unset=True)
        current = self.values(include_secrets=True)
        updates: dict[str, object] = {}
        for key, value in requested.items():
            if key in SENSITIVE_SETTING_KEYS:
                operation = SensitiveSettingPatch.model_validate(value)
                if operation.action == "keep":
                    continue
                updates[key] = (
                    operation.value.strip()
                    if operation.action == "replace" and operation.value
                    else ""
                )
            else:
                updates[key] = value

        prospective = {**current, **updates}
        self._validate_cross_fields(prospective)
        now = int(time.time())
        with self.database.transaction() as connection:
            for key, value in updates.items():
                definition = SETTING_DEFINITIONS[key]
                serialized = json.dumps(value, ensure_ascii=False, separators=(",", ":"))
                stored = self.cipher.encrypt(serialized) if definition.secret else serialized
                connection.execute(
                    "UPDATE app_settings SET value = ?, updated_at = ? WHERE key = ?",
                    (stored, now, key),
                )
        return self.public_settings()

    def _initialize_or_migrate(self, initial_values: Mapping[str, object]) -> None:
        now = int(time.time())
        with self.database.transaction() as connection:
            rows = list(
                connection.execute("SELECT key, value, is_secret FROM app_settings").fetchall()
            )
            version_row = connection.execute(
                "SELECT value FROM app_metadata WHERE key = ?",
                (SETTINGS_SCHEMA_KEY,),
            ).fetchone()
            version = int(version_row["value"]) if version_row is not None else 0

            if not rows:
                values = self._new_defaults(initial_values)
                self._replace_settings(connection, values, now)
                self._record_schema_version(connection, now)
                return

            if version < SETTINGS_SCHEMA_VERSION:
                old_values = self._decode_rows(rows)
                values = self._migrate_values(old_values, initial_values, version)
                self._replace_settings(connection, values, now)
                self._record_schema_version(connection, now)
                return

            values = self._new_defaults(initial_values)
            existing_keys = {str(row["key"]) for row in rows}
            for key, definition in SETTING_DEFINITIONS.items():
                if key in existing_keys:
                    continue
                self._insert_setting(connection, key, values[key], definition, now)

    def _new_defaults(self, initial_values: Mapping[str, object]) -> dict[str, object]:
        values = {key: definition.default for key, definition in SETTING_DEFINITIONS.items()}
        values["cache_max_mb"] = self.config.cache_max_mb
        values.update(
            {key: value for key, value in initial_values.items() if key in SETTING_DEFINITIONS}
        )
        return values

    def _decode_rows(self, rows: list[Any]) -> dict[str, object]:
        values: dict[str, object] = {}
        for row in rows:
            serialized = str(row["value"])
            if bool(row["is_secret"]):
                serialized = self.cipher.decrypt(serialized)
            values[str(row["key"])] = json.loads(serialized)
        return values

    def _migrate_legacy_values(
        self,
        old_values: Mapping[str, object],
        initial_values: Mapping[str, object],
    ) -> dict[str, object]:
        values = self._new_defaults(initial_values)
        for key in SETTING_DEFINITIONS:
            if key in old_values:
                values[key] = old_values[key]

        old_language = str(old_values.get("source_language") or "").upper()
        values["source_language"] = old_language if old_language in {"AUTO", "KO"} else "AUTO"

        old_model = str(old_values.get("ocr_model") or "").strip()
        values["ocr_model"] = (
            DEFAULT_OCR_MODEL if old_model in {"", "PaddleOCR-VL-1.5"} else old_model
        )

        old_ocr_url = str(old_values.get("ocr_api_url") or "").strip()
        values["ocr_api_url"] = old_ocr_url or DEFAULT_OCR_API_URL

        old_deeplx_url = str(old_values.get("deeplx_url") or "").strip()
        values["translation_service"] = "deeplx" if old_deeplx_url else "deepl"
        values["translation_timeout_seconds"] = old_values.get(
            "deeplx_timeout_seconds",
            SETTING_DEFINITIONS["translation_timeout_seconds"].default,
        )
        return values

    def _migrate_values(
        self,
        old_values: Mapping[str, object],
        initial_values: Mapping[str, object],
        version: int,
    ) -> dict[str, object]:
        if version < 2:
            values = self._migrate_legacy_values(old_values, initial_values)
        else:
            values = self._new_defaults(initial_values)
            for key in SETTING_DEFINITIONS:
                if key in old_values:
                    values[key] = old_values[key]

        if version < 3 and int(values.get("ocr_slice_height") or 0) == 4000:
            values["ocr_slice_height"] = 1600
        if version < 4:
            old_mode = str(old_values.get("ocr_mode") or "").strip().lower()
            values["ocr_mode"] = old_mode if old_mode in {"auto", "direct", "job"} else "auto"

            old_auth_mode = str(old_values.get("ocr_auth_mode") or "").strip().lower()
            values["ocr_auth_mode"] = (
                old_auth_mode
                if old_auth_mode in {"none", "bearer", "basic"}
                else ("bearer" if str(values.get("ocr_token") or "").strip() else "none")
            )
        return values

    def _replace_settings(self, connection: Any, values: Mapping[str, object], now: int) -> None:
        connection.execute("DELETE FROM app_settings")
        for key, definition in SETTING_DEFINITIONS.items():
            self._insert_setting(connection, key, values[key], definition, now)

    def _insert_setting(
        self,
        connection: Any,
        key: str,
        value: object,
        definition: SettingDefinition,
        now: int,
    ) -> None:
        serialized = json.dumps(value, ensure_ascii=False, separators=(",", ":"))
        stored = self.cipher.encrypt(serialized) if definition.secret else serialized
        connection.execute(
            """
            INSERT INTO app_settings(key, value, is_secret, updated_at)
            VALUES (?, ?, ?, ?)
            """,
            (key, stored, int(definition.secret), now),
        )

    @staticmethod
    def _record_schema_version(connection: Any, now: int) -> None:
        connection.execute(
            """
            INSERT INTO app_metadata(key, value, updated_at)
            VALUES (?, ?, ?)
            ON CONFLICT(key) DO UPDATE SET value = excluded.value, updated_at = excluded.updated_at
            """,
            (SETTINGS_SCHEMA_KEY, str(SETTINGS_SCHEMA_VERSION), now),
        )

    @staticmethod
    def _masked_state(value: str) -> SensitiveSettingState:
        if not value:
            return SensitiveSettingState(configured=False, masked=None)
        visible = value[-4:] if len(value) >= 4 else ""
        return SensitiveSettingState(
            configured=True,
            masked=f"••••{visible}" if visible else "••••",
        )

    @staticmethod
    def _validate_cross_fields(values: Mapping[str, object]) -> None:
        slice_height = int(values["ocr_slice_height"])
        overlap = int(values["ocr_slice_overlap"])
        if overlap >= slice_height:
            raise ValueError("OCR 分片重叠必须小于分片高度")
