from __future__ import annotations

from typing import Annotated

from fastapi import APIRouter, Depends, Path, Query, Response

from app import __version__
from app.api.auth import router as auth_router
from app.api.dependencies import get_comic_source, get_media_registry
from app.api.library import router as library_router
from app.api.settings import router as settings_router
from app.domain.comic import (
    ChapterManifest,
    ComicCategory,
    ComicDetail,
    ComicListPage,
    HomeFeed,
    RankingPage,
)
from app.errors import AppError
from app.media.registry import SourceMediaRegistry
from app.sources.base import ComicOrder, ComicSource

router = APIRouter()
router.include_router(auth_router)
router.include_router(settings_router)
router.include_router(library_router)

ComicSourceDependency = Annotated[ComicSource, Depends(get_comic_source)]
MediaRegistryDependency = Annotated[SourceMediaRegistry, Depends(get_media_registry)]


@router.get("/health", tags=["system"])
async def health() -> dict[str, str]:
    return {"status": "ok", "app": "ComicLens", "version": __version__}


@router.get("/api/home/feed", response_model=HomeFeed, tags=["catalog"])
async def home_feed(source: ComicSourceDependency, registry: MediaRegistryDependency) -> HomeFeed:
    return registry.localize_home(await source.home())


@router.get("/api/home/latest", response_model=ComicListPage, tags=["catalog"])
async def latest_comics(
    source: ComicSourceDependency,
    registry: MediaRegistryDependency,
    page: Annotated[int, Query(ge=1)] = 1,
) -> ComicListPage:
    return registry.localize_list(await source.latest(page))


@router.get("/api/comics/search", response_model=ComicListPage, tags=["catalog"])
async def search_comics(
    source: ComicSourceDependency,
    registry: MediaRegistryDependency,
    q: Annotated[str, Query(min_length=1)],
    page: Annotated[int, Query(ge=1)] = 1,
) -> ComicListPage:
    return registry.localize_list(await source.search(q, page))


@router.get(
    "/api/comics/categories",
    response_model=list[ComicCategory],
    tags=["catalog"],
)
async def comic_categories(source: ComicSourceDependency) -> list[ComicCategory]:
    return await source.categories()


@router.get(
    "/api/comics/categories/{category_id}",
    response_model=ComicListPage,
    tags=["catalog"],
)
async def comics_by_category(
    source: ComicSourceDependency,
    registry: MediaRegistryDependency,
    category_id: Annotated[str, Path(min_length=1, max_length=80)],
    page: Annotated[int, Query(ge=1)] = 1,
    order: ComicOrder = "latest",
) -> ComicListPage:
    return registry.localize_list(await source.category(category_id, page, order))


@router.get("/api/comics/ranking", response_model=RankingPage, tags=["catalog"])
async def comic_ranking(
    source: ComicSourceDependency,
    registry: MediaRegistryDependency,
    page: Annotated[int, Query(ge=1)] = 1,
) -> RankingPage:
    result = registry.localize_list(await source.ranking(page))
    return RankingPage(period="week", result=result)


@router.get("/api/comics/{comic_id}", response_model=ComicDetail, tags=["catalog"])
async def comic_detail(
    source: ComicSourceDependency,
    registry: MediaRegistryDependency,
    comic_id: Annotated[str, Path(min_length=1, max_length=160)],
) -> ComicDetail:
    return registry.localize_detail(await source.detail(comic_id))


@router.get(
    "/api/comics/{comic_id}/chapters/{chapter_id}/manifest",
    response_model=ChapterManifest,
    tags=["reader"],
)
async def chapter_manifest(
    source: ComicSourceDependency,
    registry: MediaRegistryDependency,
    comic_id: Annotated[str, Path(min_length=1, max_length=160)],
    chapter_id: Annotated[str, Path(min_length=1, max_length=160)],
) -> ChapterManifest:
    return registry.localize_manifest(await source.chapter(comic_id, chapter_id))


@router.get("/api/media/covers/{comic_id}", tags=["media"])
async def comic_cover(
    source: ComicSourceDependency,
    registry: MediaRegistryDependency,
    comic_id: Annotated[str, Path(min_length=1, max_length=160)],
) -> Response:
    source_url = registry.covers.get(comic_id)
    return await _media_response(source, source_url)


@router.get(
    "/api/media/comics/{comic_id}/chapters/{chapter_id}/pages/{page_index}/original",
    tags=["media"],
)
async def original_comic_page(
    source: ComicSourceDependency,
    registry: MediaRegistryDependency,
    comic_id: Annotated[str, Path(min_length=1, max_length=160)],
    chapter_id: Annotated[str, Path(min_length=1, max_length=160)],
    page_index: Annotated[int, Path(ge=0)],
) -> Response:
    source_url = registry.pages.get((comic_id, chapter_id, page_index))
    return await _media_response(source, source_url)


async def _media_response(source: ComicSource, source_url: str | None) -> Response:
    if source_url is None:
        raise AppError("MEDIA_NOT_FOUND", "媒体尚未登记或已失效", 404, False)
    content, media_type = await source.fetch_media(source_url)
    return Response(
        content=content,
        media_type=media_type,
        headers={"Cache-Control": "private, max-age=3600"},
    )
