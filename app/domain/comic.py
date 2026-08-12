from __future__ import annotations

from pydantic import BaseModel, ConfigDict, Field
from pydantic.alias_generators import to_camel


class ComicModel(BaseModel):
    model_config = ConfigDict(alias_generator=to_camel, populate_by_name=True)


class ChapterSummary(ComicModel):
    chapter_id: str
    title: str
    updated_label: str | None = None


class ComicSummary(ComicModel):
    comic_id: str
    title: str
    cover_url: str
    rating: float | None = None
    is_adult: bool = False
    latest_chapters: list[ChapterSummary] = Field(default_factory=list)


class ComicListPage(ComicModel):
    items: list[ComicSummary]
    page: int = Field(ge=1)
    available_pages: list[int] = Field(default_factory=list)
    has_previous: bool = False
    has_next: bool = False


class FeaturedComic(ComicModel):
    comic_id: str
    title: str
    cover_url: str
    chapter_label: str | None = None


class HomeFeed(ComicModel):
    featured: list[FeaturedComic]
    latest: ComicListPage


class ComicCategory(ComicModel):
    category_id: str
    label: str
    kind: str = "genre"
    supported_orders: list[str] = Field(default_factory=lambda: ["latest", "rating", "views"])


class ComicChapter(ComicModel):
    chapter_id: str
    title: str
    updated_label: str | None = None


class ComicDetail(ComicModel):
    comic_id: str
    title: str
    cover_url: str
    rating: float | None = None
    alternative_titles: list[str] = Field(default_factory=list)
    authors: list[str] = Field(default_factory=list)
    artists: list[str] = Field(default_factory=list)
    genres: list[str] = Field(default_factory=list)
    comic_type: str | None = None
    status: str | None = None
    summary: str = ""
    chapters: list[ComicChapter] = Field(default_factory=list)


class SourcePage(ComicModel):
    index: int = Field(ge=0)
    source_url: str
    alt: str = ""


class SourceChapterManifest(ComicModel):
    comic_id: str
    chapter_id: str
    title: str
    pages: list[SourcePage]


class ReaderPage(ComicModel):
    index: int = Field(ge=0)
    original_url: str
    translated_url: str | None = None
    translated_version: str | None = None
    width: int | None = None
    height: int | None = None
    translation_status: str = "idle"
    error: dict[str, object] | None = None


class ChapterManifest(ComicModel):
    comic_id: str
    chapter_id: str
    title: str
    pages: list[ReaderPage]


class RankingPage(ComicModel):
    period: str = "week"
    result: ComicListPage
