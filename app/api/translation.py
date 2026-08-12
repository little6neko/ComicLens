from __future__ import annotations

from typing import Annotated

from fastapi import APIRouter, Depends, Path, Query, Response

from app.api.dependencies import get_translation_manager
from app.domain.translation import (
    RetranslateRequest,
    TranslationActionResult,
    TranslationTaskState,
)
from app.errors import AppError
from app.translation.manager import TranslationManager

router = APIRouter(tags=["translation"])

ManagerDependency = Annotated[TranslationManager, Depends(get_translation_manager)]
ComicId = Annotated[str, Path(min_length=1, max_length=160)]
ChapterId = Annotated[str, Path(min_length=1, max_length=160)]


@router.get(
    "/api/comics/{comic_id}/chapters/{chapter_id}/translation",
    response_model=TranslationTaskState,
)
async def translation_state(
    comic_id: ComicId,
    chapter_id: ChapterId,
    manager: ManagerDependency,
) -> TranslationTaskState:
    return manager.state(comic_id, chapter_id)


@router.post(
    "/api/comics/{comic_id}/chapters/{chapter_id}/translation/start",
    response_model=TranslationActionResult,
)
async def start_translation(
    comic_id: ComicId,
    chapter_id: ChapterId,
    manager: ManagerDependency,
) -> TranslationActionResult:
    return TranslationActionResult(task=await manager.start(comic_id, chapter_id))


@router.post(
    "/api/comics/{comic_id}/chapters/{chapter_id}/translation/pause",
    response_model=TranslationActionResult,
)
async def pause_translation(
    comic_id: ComicId,
    chapter_id: ChapterId,
    manager: ManagerDependency,
) -> TranslationActionResult:
    return TranslationActionResult(task=await manager.pause(comic_id, chapter_id))


@router.post(
    "/api/comics/{comic_id}/chapters/{chapter_id}/translation/retranslate",
    response_model=TranslationActionResult,
)
async def retranslate_chapter(
    payload: RetranslateRequest,
    comic_id: ComicId,
    chapter_id: ChapterId,
    manager: ManagerDependency,
) -> TranslationActionResult:
    if not payload.confirmed:
        raise AppError(
            "CONFIRMATION_REQUIRED",
            "重新翻译本话需要确认",
            422,
            False,
        )
    return TranslationActionResult(task=await manager.retranslate(comic_id, chapter_id))


@router.post(
    "/api/comics/{comic_id}/chapters/{chapter_id}/translation/pages/{page_index}/retry",
    response_model=TranslationActionResult,
)
async def retry_translation_page(
    comic_id: ComicId,
    chapter_id: ChapterId,
    page_index: Annotated[int, Path(ge=0)],
    manager: ManagerDependency,
) -> TranslationActionResult:
    return TranslationActionResult(task=await manager.retry_page(comic_id, chapter_id, page_index))


@router.post(
    "/api/comics/{comic_id}/chapters/{chapter_id}/translation/pages/"
    "{page_index}/segments/{segment_index}/retry",
    response_model=TranslationActionResult,
)
async def retry_translation_segment(
    comic_id: ComicId,
    chapter_id: ChapterId,
    page_index: Annotated[int, Path(ge=0)],
    segment_index: Annotated[int, Path(ge=0)],
    manager: ManagerDependency,
) -> TranslationActionResult:
    return TranslationActionResult(
        task=await manager.retry_segment(
            comic_id,
            chapter_id,
            page_index,
            segment_index,
        )
    )


@router.get("/api/media/comics/{comic_id}/chapters/{chapter_id}/pages/{page_index}/translated")
async def translated_comic_page(
    comic_id: ComicId,
    chapter_id: ChapterId,
    page_index: Annotated[int, Path(ge=0)],
    manager: ManagerDependency,
    v: Annotated[str, Query(min_length=16, max_length=128)],
) -> Response:
    media = manager.translated_media(comic_id, chapter_id, page_index, v)
    return Response(
        content=media.content,
        media_type=media.media_type,
        headers={
            "Cache-Control": "private, max-age=31536000, immutable",
            "ETag": f'"{media.etag}"',
        },
    )


@router.get(
    "/api/media/comics/{comic_id}/chapters/{chapter_id}/pages/"
    "{page_index}/translated/parts/{part_index}"
)
async def translated_comic_page_part(
    comic_id: ComicId,
    chapter_id: ChapterId,
    page_index: Annotated[int, Path(ge=0)],
    part_index: Annotated[int, Path(ge=0)],
    manager: ManagerDependency,
    v: Annotated[str, Query(min_length=16, max_length=128)],
) -> Response:
    media = manager.translated_part_media(
        comic_id,
        chapter_id,
        page_index,
        part_index,
        v,
    )
    return Response(
        content=media.content,
        media_type=media.media_type,
        headers={
            "Cache-Control": "private, max-age=31536000, immutable",
            "ETag": f'"{media.etag}"',
        },
    )


@router.get(
    "/api/media/comics/{comic_id}/chapters/{chapter_id}/pages/"
    "{page_index}/segments/{segment_index}/translated"
)
async def translated_comic_segment(
    comic_id: ComicId,
    chapter_id: ChapterId,
    page_index: Annotated[int, Path(ge=0)],
    segment_index: Annotated[int, Path(ge=0)],
    manager: ManagerDependency,
    v: Annotated[str, Query(min_length=16, max_length=128)],
) -> Response:
    media = manager.translated_segment_media(
        comic_id,
        chapter_id,
        page_index,
        segment_index,
        v,
    )
    return Response(
        content=media.content,
        media_type=media.media_type,
        headers={
            "Cache-Control": "private, max-age=31536000, immutable",
            "ETag": f'"{media.etag}"',
        },
    )
