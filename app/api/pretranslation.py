from __future__ import annotations

from typing import Annotated

from fastapi import APIRouter, Depends, Path

from app.api.dependencies import (
    get_comic_source,
    get_pretranslation_coordinator,
    get_pretranslation_repository,
    get_translation_repository,
)
from app.domain.pretranslation import (
    ChapterTranslationOverview,
    ComicTranslationOverview,
    CreateTranslationBatchRequest,
    CreateTranslationBatchResult,
    TranslationBatchActionResult,
    TranslationBatchSummary,
)
from app.errors import AppError
from app.repositories.pretranslation import PretranslationRepository
from app.repositories.translation import TranslationRepository
from app.sources.base import ComicSource
from app.translation.pretranslation import PretranslationCoordinator

router = APIRouter(tags=["pretranslation"])

ComicId = Annotated[str, Path(min_length=1, max_length=160)]
BatchId = Annotated[str, Path(min_length=1, max_length=64)]
SourceDependency = Annotated[ComicSource, Depends(get_comic_source)]
CoordinatorDependency = Annotated[
    PretranslationCoordinator,
    Depends(get_pretranslation_coordinator),
]
BatchRepositoryDependency = Annotated[
    PretranslationRepository,
    Depends(get_pretranslation_repository),
]
TranslationRepositoryDependency = Annotated[
    TranslationRepository,
    Depends(get_translation_repository),
]


@router.get(
    "/api/comics/{comic_id}/translation-overview",
    response_model=ComicTranslationOverview,
)
async def comic_translation_overview(
    comic_id: ComicId,
    source: SourceDependency,
    coordinator: CoordinatorDependency,
    batches: BatchRepositoryDependency,
    translations: TranslationRepositoryDependency,
) -> ComicTranslationOverview:
    detail = await source.detail(comic_id)
    local_statuses = translations.chapter_overview_statuses(comic_id)
    open_batch = batches.open_batch_for_comic(comic_id)
    batch_summary = (
        coordinator.summary(str(open_batch["batch_id"])) if open_batch is not None else None
    )
    batch_items = (
        {
            item.chapter_id: item
            for item in batches.batch_item_summaries(str(open_batch["batch_id"]))
        }
        if open_batch is not None
        else {}
    )
    positions = {
        chapter.chapter_id: position
        for position, chapter in enumerate(reversed(detail.chapters))
    }
    chapters = [
        ChapterTranslationOverview(
            chapter_id=chapter.chapter_id,
            chapter_title=chapter.title,
            position=positions[chapter.chapter_id],
            status=local_statuses.get(chapter.chapter_id, "not_started"),
            requires_work=local_statuses.get(chapter.chapter_id) != "completed",
            batch_item=batch_items.get(chapter.chapter_id),
        )
        for chapter in detail.chapters
    ]
    return ComicTranslationOverview(
        comic_id=comic_id,
        chapters=chapters,
        batch=batch_summary,
    )


@router.post(
    "/api/comics/{comic_id}/translation-batches",
    response_model=CreateTranslationBatchResult,
)
async def create_translation_batch(
    payload: CreateTranslationBatchRequest,
    comic_id: ComicId,
    source: SourceDependency,
    coordinator: CoordinatorDependency,
    batches: BatchRepositoryDependency,
    translations: TranslationRepositoryDependency,
) -> CreateTranslationBatchResult:
    if batches.open_batch_for_comic(comic_id) is not None:
        raise AppError(
            "TRANSLATION_BATCH_EXISTS",
            "该漫画已有未结束的预先翻译批次",
            409,
            False,
        )
    detail = await source.detail(comic_id)
    catalog = {chapter.chapter_id: chapter for chapter in detail.chapters}
    selected_ids = list(dict.fromkeys(payload.chapter_ids))
    unknown_ids = [chapter_id for chapter_id in selected_ids if chapter_id not in catalog]
    if unknown_ids:
        raise AppError(
            "TRANSLATION_BATCH_CHAPTER_NOT_FOUND",
            "选择中包含目录里不存在的章节",
            422,
            False,
        )

    selected_set = set(selected_ids)
    ordered_chapters = [
        (chapter.chapter_id, chapter.title)
        for chapter in reversed(detail.chapters)
        if chapter.chapter_id in selected_set
    ]
    local_statuses = translations.chapter_overview_statuses(comic_id)
    work_count = sum(
        local_statuses.get(chapter_id) != "completed" for chapter_id in selected_ids
    )
    if work_count == 0:
        return CreateTranslationBatchResult(
            batch=None,
            selected_count=len(selected_ids),
            work_count=0,
            no_work=True,
        )
    batch = coordinator.create_batch(comic_id, detail.title, ordered_chapters)
    return CreateTranslationBatchResult(
        batch=batch,
        selected_count=len(selected_ids),
        work_count=work_count,
        no_work=False,
    )


@router.get(
    "/api/translation-batches/background",
    response_model=list[TranslationBatchSummary],
)
async def background_translation_batches(
    coordinator: CoordinatorDependency,
) -> list[TranslationBatchSummary]:
    return coordinator.background_batches()


@router.post(
    "/api/translation-batches/{batch_id}/pause",
    response_model=TranslationBatchActionResult,
)
async def pause_translation_batch(
    batch_id: BatchId,
    coordinator: CoordinatorDependency,
) -> TranslationBatchActionResult:
    return TranslationBatchActionResult(batch=coordinator.pause(batch_id))


@router.post(
    "/api/translation-batches/{batch_id}/resume",
    response_model=TranslationBatchActionResult,
)
async def resume_translation_batch(
    batch_id: BatchId,
    coordinator: CoordinatorDependency,
) -> TranslationBatchActionResult:
    return TranslationBatchActionResult(batch=coordinator.resume(batch_id))


@router.post(
    "/api/translation-batches/{batch_id}/cancel-pending",
    response_model=TranslationBatchActionResult,
)
async def cancel_pending_translation_batch(
    batch_id: BatchId,
    coordinator: CoordinatorDependency,
) -> TranslationBatchActionResult:
    return TranslationBatchActionResult(batch=coordinator.cancel_pending(batch_id))


@router.post(
    "/api/translation-batches/{batch_id}/retry-failed",
    response_model=TranslationBatchActionResult,
)
async def retry_failed_translation_batch(
    batch_id: BatchId,
    coordinator: CoordinatorDependency,
) -> TranslationBatchActionResult:
    return TranslationBatchActionResult(batch=coordinator.retry_failed(batch_id))


@router.post(
    "/api/translation-batches/{batch_id}/close",
    response_model=TranslationBatchActionResult,
)
async def close_translation_batch(
    batch_id: BatchId,
    coordinator: CoordinatorDependency,
) -> TranslationBatchActionResult:
    return TranslationBatchActionResult(batch=coordinator.close(batch_id))
