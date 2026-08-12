from __future__ import annotations

from typing import Literal

from pydantic import Field

from app.domain.comic import ComicModel

TranslationTaskStatus = Literal[
    "idle",
    "queued",
    "running",
    "stopping_after_page",
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


class TranslationError(ComicModel):
    stage: str
    code: str
    message: str
    retryable: bool = True


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


class TranslationTaskState(ComicModel):
    comic_id: str
    chapter_id: str
    generation_id: str | None = None
    kind: Literal["normal", "retranslate", "retry"] = "normal"
    status: TranslationTaskStatus = "idle"
    stop_requested: bool = False
    current_page_index: int | None = None
    total_pages: int = 0
    completed_pages: int = 0
    failed_pages: int = 0
    pages: list[TranslationPageState] = Field(default_factory=list)


class TranslationActionResult(ComicModel):
    task: TranslationTaskState


class RetranslateRequest(ComicModel):
    confirmed: bool
