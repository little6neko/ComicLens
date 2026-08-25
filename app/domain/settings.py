from __future__ import annotations

from typing import Literal

from pydantic import ConfigDict, Field, model_validator

from app.domain.comic import ComicModel


class SensitiveSettingState(ComicModel):
    configured: bool
    masked: str | None = None


class SensitiveSettingPatch(ComicModel):
    action: Literal["keep", "replace", "clear"]
    value: str | None = None

    @model_validator(mode="after")
    def validate_action_value(self) -> SensitiveSettingPatch:
        if self.action == "replace" and not (self.value and self.value.strip()):
            raise ValueError("replace 操作必须提供非空 value")
        if self.action != "replace" and self.value is not None:
            raise ValueError("只有 replace 操作可以提供 value")
        return self


class ServerSettings(ComicModel):
    page_direction: Literal["ltr", "rtl"]
    source_language: Literal["AUTO", "EN", "KO"]
    target_language: Literal["ZH-HANS"] = "ZH-HANS"
    ocr_mode: Literal["auto", "direct", "job"]
    ocr_auth_mode: Literal["none", "bearer", "basic"]
    ocr_api_url: str
    ocr_token: SensitiveSettingState
    ocr_basic_username: str
    ocr_basic_password: SensitiveSettingState
    ocr_model: str
    ocr_poll_interval_seconds: float
    ocr_timeout_seconds: float
    ocr_concurrency: int
    translation_service: Literal["deepl", "deeplx"]
    deepl_api_key: SensitiveSettingState
    deeplx_url: SensitiveSettingState
    translation_timeout_seconds: float
    translation_concurrency: int
    proxy_url: str
    proxy_username: str
    proxy_password: SensitiveSettingState
    long_image_threshold: int
    ocr_slice_height: int
    ocr_slice_overlap: int
    reading_slice_height: int
    cache_max_mb: int
    access_password_enabled: bool
    public_listener_warning: bool


class ServerSettingsPatch(ComicModel):
    model_config = ConfigDict(extra="forbid")

    page_direction: Literal["ltr", "rtl"] | None = None
    source_language: Literal["AUTO", "EN", "KO"] | None = None
    ocr_mode: Literal["auto", "direct", "job"] | None = None
    ocr_auth_mode: Literal["none", "bearer", "basic"] | None = None
    ocr_api_url: str | None = None
    ocr_token: SensitiveSettingPatch | None = None
    ocr_basic_username: str | None = Field(default=None, max_length=200)
    ocr_basic_password: SensitiveSettingPatch | None = None
    ocr_model: str | None = Field(default=None, max_length=200)
    ocr_poll_interval_seconds: float | None = Field(default=None, ge=0.2, le=60)
    ocr_timeout_seconds: float | None = Field(default=None, ge=1, le=3600)
    ocr_concurrency: int | None = Field(default=None, ge=1, le=16)
    translation_service: Literal["deepl", "deeplx"] | None = None
    deepl_api_key: SensitiveSettingPatch | None = None
    deeplx_url: SensitiveSettingPatch | None = None
    translation_timeout_seconds: float | None = Field(default=None, ge=1, le=600)
    translation_concurrency: int | None = Field(default=None, ge=1, le=16)
    proxy_url: str | None = None
    proxy_username: str | None = Field(default=None, max_length=200)
    proxy_password: SensitiveSettingPatch | None = None
    long_image_threshold: int | None = Field(default=None, ge=1000, le=100000)
    ocr_slice_height: int | None = Field(default=None, ge=500, le=50000)
    ocr_slice_overlap: int | None = Field(default=None, ge=0, le=5000)
    reading_slice_height: int | None = Field(default=None, ge=500, le=50000)
    cache_max_mb: int | None = Field(default=None, ge=128, le=102400)


class AuthConfig(ComicModel):
    enabled: bool


class AuthSession(ComicModel):
    enabled: bool
    authenticated: bool


class LoginRequest(ComicModel):
    password: str = Field(min_length=1, max_length=1024)
