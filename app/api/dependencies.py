from __future__ import annotations

from typing import cast

from fastapi import Request

from app.application.settings import SettingsService
from app.cache.storage import MediaCache
from app.media.registry import SourceMediaRegistry
from app.repositories.database import Database
from app.repositories.library import LibraryRepository
from app.security.access import AccessGate, LoginRateLimiter
from app.sources.base import ComicSource
from app.translation.manager import TranslationManager


def get_comic_source(request: Request) -> ComicSource:
    return cast(ComicSource, request.app.state.comic_source)


def get_media_registry(request: Request) -> SourceMediaRegistry:
    return cast(SourceMediaRegistry, request.app.state.media_registry)


def get_media_cache(request: Request) -> MediaCache:
    return cast(MediaCache, request.app.state.media_cache)


def get_database(request: Request) -> Database:
    return cast(Database, request.app.state.database)


def get_library_repository(request: Request) -> LibraryRepository:
    return cast(LibraryRepository, request.app.state.library_repository)


def get_settings_service(request: Request) -> SettingsService:
    return cast(SettingsService, request.app.state.settings_service)


def get_access_gate(request: Request) -> AccessGate:
    return cast(AccessGate, request.app.state.access_gate)


def get_login_rate_limiter(request: Request) -> LoginRateLimiter:
    return cast(LoginRateLimiter, request.app.state.login_rate_limiter)


def get_translation_manager(request: Request) -> TranslationManager:
    return cast(TranslationManager, request.app.state.translation_manager)
