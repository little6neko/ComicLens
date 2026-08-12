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


@dataclass(frozen=True, slots=True)
class SettingDefinition:
    default: object
    secret: bool = False


SETTING_DEFINITIONS: dict[str, SettingDefinition] = {
    "theme": SettingDefinition("system"),
    "reading_mode": SettingDefinition("strip"),
    "page_direction": SettingDefinition("ltr"),
    "realtime_translation_default": SettingDefinition(False),
    "source_language": SettingDefinition("EN"),
    "ocr_mode": SettingDefinition("auto"),
    "ocr_auth_mode": SettingDefinition("none"),
    "ocr_api_url": SettingDefinition("", True),
    "ocr_token": SettingDefinition("", True),
    "ocr_basic_username": SettingDefinition(""),
    "ocr_basic_password": SettingDefinition("", True),
    "ocr_model": SettingDefinition(""),
    "ocr_poll_interval_seconds": SettingDefinition(2.0),
    "ocr_timeout_seconds": SettingDefinition(180.0),
    "ocr_concurrency": SettingDefinition(1),
    "deeplx_url": SettingDefinition("", True),
    "deeplx_timeout_seconds": SettingDefinition(30.0),
    "translation_concurrency": SettingDefinition(2),
    "fallback_proxy_url": SettingDefinition("", True),
    "long_image_threshold": SettingDefinition(8000),
    "ocr_slice_height": SettingDefinition(4000),
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
        self._initialize(config.initial_settings)
        # Fail during startup if the database and secrets.key do not belong together.
        self.values(include_secrets=True)

    def public_settings(self) -> ServerSettings:
        values = self.values(include_secrets=False)
        return ServerSettings(
            **values,
            target_language="ZH",
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

    def _initialize(self, initial_values: Mapping[str, object]) -> None:
        initial = {key: definition.default for key, definition in SETTING_DEFINITIONS.items()}
        initial["cache_max_mb"] = self.config.cache_max_mb
        initial.update(
            {key: value for key, value in initial_values.items() if key in SETTING_DEFINITIONS}
        )
        now = int(time.time())
        with self.database.transaction() as connection:
            for key, definition in SETTING_DEFINITIONS.items():
                serialized = json.dumps(initial[key], ensure_ascii=False, separators=(",", ":"))
                stored = self.cipher.encrypt(serialized) if definition.secret else serialized
                connection.execute(
                    """
                    INSERT OR IGNORE INTO app_settings(key, value, is_secret, updated_at)
                    VALUES (?, ?, ?, ?)
                    """,
                    (key, stored, int(definition.secret), now),
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
