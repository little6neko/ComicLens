from __future__ import annotations

from typing import Literal

from pydantic import Field

from app.domain.comic import ComicModel
from app.domain.translation import TranslationTaskProgress

TranslationBatchStatus = Literal[
    "queued",
    "running",
    "pausing",
    "paused",
    "cancelling",
    "completed",
    "completed_with_errors",
    "cancelled",
    "failed",
]

TranslationBatchItemStatus = Literal[
    "pending",
    "running",
    "completed",
    "skipped",
    "failed",
    "cancelled",
]

TranslationBatchPauseReason = Literal["user", "config"]

ChapterTranslationOverviewStatus = Literal[
    "not_started",
    "active",
    "paused",
    "completed",
    "needs_retry",
    "failed",
]


class CreateTranslationBatchRequest(ComicModel):
    chapter_ids: list[str] = Field(min_length=1, max_length=5000)


class TranslationBatchItemSummary(ComicModel):
    batch_item_id: str
    chapter_id: str
    chapter_title: str
    position: int = Field(ge=0)
    status: TranslationBatchItemStatus
    attempts: int = Field(ge=0)
    error_code: str | None = None
    error_summary: str | None = None


class TranslationBatchTaskSummary(TranslationTaskProgress):
    pass


class TranslationBatchSummary(ComicModel):
    batch_id: str
    comic_id: str
    comic_title: str
    status: TranslationBatchStatus
    pause_reason: TranslationBatchPauseReason | None = None
    interactive_yielded: bool = False
    error_code: str | None = None
    error_summary: str | None = None
    total_chapters: int = Field(ge=0)
    pending_chapters: int = Field(ge=0)
    running_chapters: int = Field(ge=0)
    completed_chapters: int = Field(ge=0)
    available_chapters: int = Field(ge=0)
    skipped_chapters: int = Field(ge=0)
    failed_chapters: int = Field(ge=0)
    cancelled_chapters: int = Field(ge=0)
    current_item: TranslationBatchItemSummary | None = None
    current_task: TranslationBatchTaskSummary | None = None
    created_at: int
    updated_at: int


class ChapterTranslationOverview(ComicModel):
    chapter_id: str
    chapter_title: str
    position: int = Field(ge=0)
    status: ChapterTranslationOverviewStatus
    requires_work: bool
    batch_item: TranslationBatchItemSummary | None = None


class ComicTranslationOverview(ComicModel):
    comic_id: str
    chapters: list[ChapterTranslationOverview]
    batch: TranslationBatchSummary | None = None


class CreateTranslationBatchResult(ComicModel):
    batch: TranslationBatchSummary | None = None
    selected_count: int = Field(ge=0)
    work_count: int = Field(ge=0)
    no_work: bool = False


class TranslationBatchActionResult(ComicModel):
    batch: TranslationBatchSummary
