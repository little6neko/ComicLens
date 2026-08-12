from __future__ import annotations

from typing import Annotated

from fastapi import APIRouter, Depends, Request

from app.api.dependencies import get_settings_service
from app.application.settings import SettingsService
from app.domain.settings import ServerSettings, ServerSettingsPatch
from app.errors import AppError

router = APIRouter(prefix="/api/settings", tags=["settings"])

SettingsDependency = Annotated[SettingsService, Depends(get_settings_service)]


@router.get("", response_model=ServerSettings)
async def get_settings(settings: SettingsDependency) -> ServerSettings:
    return settings.public_settings()


@router.patch("", response_model=ServerSettings)
async def patch_settings(
    payload: ServerSettingsPatch,
    settings: SettingsDependency,
    request: Request,
) -> ServerSettings:
    try:
        updated = settings.patch(payload)
        request.app.state.media_cache.max_bytes = updated.cache_max_mb * 1024 * 1024
        request.app.state.media_cache.enforce_limit()
        return updated
    except ValueError as exc:
        raise AppError("VALIDATION_ERROR", str(exc), 422, False) from exc
