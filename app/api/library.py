from __future__ import annotations

from typing import Annotated

from fastapi import APIRouter, Depends, Path, Response

from app.api.dependencies import (
    get_comic_source,
    get_library_repository,
    get_media_registry,
)
from app.domain.comic import ChapterSummary, ComicSummary
from app.domain.library import (
    ComicSnapshotInput,
    FavoriteItem,
    HistoryItem,
    HistoryUpdate,
    ReadChapterState,
    ReadChapterUpdate,
)
from app.errors import AppError
from app.media.registry import SourceMediaRegistry
from app.repositories.library import LibraryRepository
from app.sources.base import ComicSource

router = APIRouter(tags=["library"])

LibraryDependency = Annotated[LibraryRepository, Depends(get_library_repository)]
RegistryDependency = Annotated[SourceMediaRegistry, Depends(get_media_registry)]
SourceDependency = Annotated[ComicSource, Depends(get_comic_source)]
ComicId = Annotated[str, Path(min_length=1, max_length=160)]
ChapterId = Annotated[str, Path(min_length=1, max_length=160)]


@router.get("/api/favorites", response_model=list[FavoriteItem])
async def list_favorites(
    library: LibraryDependency, registry: RegistryDependency
) -> list[FavoriteItem]:
    return [registry.localize_favorite(item) for item in library.list_favorites()]


@router.put("/api/favorites/{comic_id}", response_model=FavoriteItem)
async def save_favorite(
    payload: ComicSnapshotInput,
    comic_id: ComicId,
    source: SourceDependency,
    library: LibraryDependency,
    registry: RegistryDependency,
) -> FavoriteItem:
    summary, source_url = await _resolve_snapshot(comic_id, payload, source, registry)
    return registry.localize_favorite(library.save_favorite(comic_id, summary, source_url))


@router.delete("/api/favorites/{comic_id}", status_code=204)
async def delete_favorite(comic_id: ComicId, library: LibraryDependency) -> Response:
    library.delete_favorite(comic_id)
    return Response(status_code=204)


@router.delete("/api/favorites", status_code=204)
async def clear_favorites(library: LibraryDependency) -> Response:
    library.clear_favorites()
    return Response(status_code=204)


@router.get("/api/history", response_model=list[HistoryItem])
async def list_history(
    library: LibraryDependency, registry: RegistryDependency
) -> list[HistoryItem]:
    return [registry.localize_history(item) for item in library.list_history()]


@router.put("/api/history/{comic_id}", response_model=HistoryItem)
async def save_history(
    payload: HistoryUpdate,
    comic_id: ComicId,
    source: SourceDependency,
    library: LibraryDependency,
    registry: RegistryDependency,
) -> HistoryItem:
    _, source_url = await _resolve_snapshot(comic_id, payload, source, registry)
    return registry.localize_history(library.save_history(comic_id, payload, source_url))


@router.delete("/api/history/{comic_id}", status_code=204)
async def delete_history(comic_id: ComicId, library: LibraryDependency) -> Response:
    library.delete_history(comic_id)
    return Response(status_code=204)


@router.delete("/api/history", status_code=204)
async def clear_history(library: LibraryDependency) -> Response:
    library.clear_history()
    return Response(status_code=204)


@router.get("/api/comics/{comic_id}/read-chapters", response_model=ReadChapterState)
async def list_read_chapters(comic_id: ComicId, library: LibraryDependency) -> ReadChapterState:
    return ReadChapterState(
        comic_id=comic_id,
        chapter_ids=library.read_chapters(comic_id),
    )


@router.put(
    "/api/comics/{comic_id}/read-chapters/{chapter_id}",
    response_model=ReadChapterState,
)
async def update_read_chapter(
    payload: ReadChapterUpdate,
    comic_id: ComicId,
    chapter_id: ChapterId,
    library: LibraryDependency,
) -> ReadChapterState:
    library.set_chapter_read(comic_id, chapter_id, payload.read)
    return ReadChapterState(
        comic_id=comic_id,
        chapter_ids=library.read_chapters(comic_id),
    )


async def _resolve_snapshot(
    comic_id: str,
    payload: ComicSnapshotInput,
    source: ComicSource,
    registry: SourceMediaRegistry,
) -> tuple[ComicSummary, str]:
    source_url = registry.cover_source(comic_id)
    if source_url is None:
        detail = await source.detail(comic_id)
        registry.localize_detail(detail)
        source_url = registry.cover_source(comic_id)
    if source_url is None:
        raise AppError("COVER_NOT_FOUND", "Comic 封面来源无法识别", 502, True)
    summary = ComicSummary(
        comic_id=comic_id,
        title=payload.title,
        cover_url=source_url,
        rating=payload.rating,
        is_adult=payload.is_adult,
        latest_chapters=[ChapterSummary.model_validate(item) for item in payload.latest_chapters],
    )
    return summary, source_url
