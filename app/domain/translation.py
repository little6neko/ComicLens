from __future__ import annotations

from typing import Literal

from pydantic import Field

from app.domain.comic import ComicModel

TranslationTaskStatus = Literal[
    "idle",
    "preparing",
    "queued",
    "running",
    "stopping_after_page",
    "stopping_after_segment",
    "paused",
    "completed",
    "completed_with_errors",
    "failed",
]

TranslationPageStage = Literal[
    "pending",
    "downloading",
    "ocr",
    "translating",
    "rendering",
    "completed",
    "failed",
]

TranslationSegmentStage = Literal[
    "pending",
    "ocr",
    "translating",
    "rendering",
    "completed",
    "failed",
]

BackgroundTranslationStage = Literal[
    "preparing",
    "queued",
    "ocr",
    "translating",
    "rendering",
    "stopping",
    "processing",
    "needs_retry",
]


class TranslationError(ComicModel):
    stage: str
    code: str
    message: str
    retryable: bool = True


class TranslationLayer(ComicModel):
    kind: Literal["page", "segment"]
    generation_id: str
    segment_index: int | None = None
    top: int = Field(ge=0)
    bottom: int = Field(gt=0)
    source_width: int = Field(gt=0)
    source_height: int = Field(gt=0)
    url: str
    version: str


class TranslationSegmentState(ComicModel):
    page_index: int = Field(ge=0)
    segment_index: int = Field(ge=0)
    global_index: int = Field(ge=0)
    status: TranslationSegmentStage
    display_top: int = Field(ge=0)
    display_bottom: int = Field(gt=0)
    source_width: int = Field(gt=0)
    source_height: int = Field(gt=0)
    translated_url: str | None = None
    translated_version: str | None = None
    attempts: int = 0
    error: TranslationError | None = None


class CurrentTranslationSegment(ComicModel):
    page_index: int = Field(ge=0)
    segment_index: int = Field(ge=0)


class TranslationPageState(ComicModel):
    page_index: int = Field(ge=0)
    status: TranslationPageStage
    translated_url: str | None = None
    translated_part_urls: list[str] = Field(default_factory=list)
    translated_version: str | None = None
    width: int | None = None
    height: int | None = None
    attempts: int = 0
    error: TranslationError | None = None
    segments: list[TranslationSegmentState] = Field(default_factory=list)
    translation_layers: list[TranslationLayer] = Field(default_factory=list)


class TranslationTaskState(ComicModel):
    comic_id: str
    chapter_id: str
    generation_id: str | None = None
    kind: Literal["normal", "retranslate", "retry"] = "normal"
    status: TranslationTaskStatus = "idle"
    stop_requested: bool = False
    current_page_index: int | None = None
    current_segment: CurrentTranslationSegment | None = None
    total_pages: int = 0
    completed_pages: int = 0
    failed_pages: int = 0
    planning_complete: bool = False
    total_segments: int = 0
    completed_segments: int = 0
    failed_segments: int = 0
    pages: list[TranslationPageState] = Field(default_factory=list)


class TranslationActionResult(ComicModel):
    task: TranslationTaskState


class RetryFailedTranslationResult(ComicModel):
    task: TranslationTaskState
    retried_count: int = Field(ge=0)


class TranslationTaskProgress(ComicModel):
    generation_id: str
    status: TranslationTaskStatus
    stage: BackgroundTranslationStage
    current_page_index: int | None = None
    current_segment: CurrentTranslationSegment | None = None
    planning_complete: bool = False
    total_pages: int = 0
    prepared_pages: int = 0
    completed_pages: int = 0
    failed_pages: int = 0
    total_segments: int = 0
    completed_segments: int = 0
    failed_segments: int = 0


class BackgroundTranslationTask(TranslationTaskProgress):
    comic_id: str
    chapter_id: str
    comic_title: str
    chapter_title: str
    kind: Literal["normal", "retranslate", "retry"] = "normal"


class ForceStopTranslationResult(ComicModel):
    comic_id: str
    chapter_id: str
    stopped_generations: int = 0


class RetranslateRequest(ComicModel):
    confirmed: bool
