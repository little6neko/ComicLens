from __future__ import annotations

from pydantic import Field, model_validator

from app.domain.comic import ChapterSummary, ComicModel, ComicSummary


class ComicSnapshotInput(ComicModel):
    title: str = Field(min_length=1, max_length=500)
    rating: float | None = Field(default=None, ge=0, le=10)
    is_adult: bool = False
    latest_chapters: list[ChapterSummary] = Field(default_factory=list, max_length=20)


class FavoriteItem(ComicModel):
    comic: ComicSummary
    favorited_at: int


class HistoryUpdate(ComicSnapshotInput):
    chapter_id: str = Field(min_length=1, max_length=160)
    chapter_title: str = Field(min_length=1, max_length=500)
    page_index: int = Field(ge=0)
    total_pages: int = Field(ge=1, le=5000)

    @model_validator(mode="after")
    def validate_progress(self) -> HistoryUpdate:
        if self.page_index >= self.total_pages:
            raise ValueError("pageIndex 必须小于 totalPages")
        return self


class HistoryItem(ComicModel):
    comic: ComicSummary
    chapter_id: str
    chapter_title: str
    page_index: int
    total_pages: int
    updated_at: int


class ReadChapterState(ComicModel):
    comic_id: str
    chapter_ids: list[str]


class ReadChapterUpdate(ComicModel):
    read: bool = True
