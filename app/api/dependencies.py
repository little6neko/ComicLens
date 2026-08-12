from __future__ import annotations

from typing import cast

from fastapi import Request

from app.media.registry import SourceMediaRegistry
from app.sources.base import ComicSource


def get_comic_source(request: Request) -> ComicSource:
    return cast(ComicSource, request.app.state.comic_source)


def get_media_registry(request: Request) -> SourceMediaRegistry:
    return cast(SourceMediaRegistry, request.app.state.media_registry)
