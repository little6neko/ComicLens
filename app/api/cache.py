from __future__ import annotations

import asyncio
from typing import Annotated

from fastapi import APIRouter, Depends, Path, Response

from app.api.dependencies import get_media_cache
from app.cache.storage import MediaCache
from app.domain.cache import CacheStats

router = APIRouter(prefix="/api/system/cache", tags=["system"])

CacheDependency = Annotated[MediaCache, Depends(get_media_cache)]


@router.get("", response_model=CacheStats)
async def cache_stats(cache: CacheDependency) -> CacheStats:
    return cache.stats()


@router.delete("", status_code=204)
async def clear_cache(cache: CacheDependency) -> Response:
    await asyncio.to_thread(cache.clear)
    return Response(status_code=204)


@router.delete(
    "/comics/{comic_id}/chapters/{chapter_id}",
    status_code=204,
)
async def delete_chapter_cache(
    cache: CacheDependency,
    comic_id: Annotated[str, Path(min_length=1, max_length=160)],
    chapter_id: Annotated[str, Path(min_length=1, max_length=160)],
) -> Response:
    await asyncio.to_thread(cache.remove_chapter, comic_id, chapter_id)
    return Response(status_code=204)
