from __future__ import annotations

from dataclasses import dataclass, field

from app.domain.comic import (
    ChapterManifest,
    ComicDetail,
    ComicListPage,
    ComicSummary,
    FeaturedComic,
    HomeFeed,
    ReaderPage,
    SourceChapterManifest,
)


@dataclass(slots=True)
class SourceMediaRegistry:
    covers: dict[str, str] = field(default_factory=dict)
    pages: dict[tuple[str, str, int], str] = field(default_factory=dict)

    def localize_summary(self, item: ComicSummary) -> ComicSummary:
        if item.cover_url:
            self.covers[item.comic_id] = item.cover_url
        return item.model_copy(update={"cover_url": self.cover_url(item.comic_id)})

    def localize_list(self, result: ComicListPage) -> ComicListPage:
        return result.model_copy(
            update={"items": [self.localize_summary(item) for item in result.items]}
        )

    def localize_featured(self, item: FeaturedComic) -> FeaturedComic:
        if item.cover_url:
            self.covers[item.comic_id] = item.cover_url
        return item.model_copy(update={"cover_url": self.cover_url(item.comic_id)})

    def localize_home(self, result: HomeFeed) -> HomeFeed:
        return result.model_copy(
            update={
                "featured": [self.localize_featured(item) for item in result.featured],
                "latest": self.localize_list(result.latest),
            }
        )

    def localize_detail(self, result: ComicDetail) -> ComicDetail:
        if result.cover_url:
            self.covers[result.comic_id] = result.cover_url
        return result.model_copy(update={"cover_url": self.cover_url(result.comic_id)})

    def localize_manifest(self, result: SourceChapterManifest) -> ChapterManifest:
        pages: list[ReaderPage] = []
        for page in result.pages:
            self.pages[(result.comic_id, result.chapter_id, page.index)] = page.source_url
            pages.append(
                ReaderPage(
                    index=page.index,
                    original_url=self.original_url(result.comic_id, result.chapter_id, page.index),
                )
            )
        return ChapterManifest(
            comic_id=result.comic_id,
            chapter_id=result.chapter_id,
            title=result.title,
            pages=pages,
        )

    @staticmethod
    def cover_url(comic_id: str) -> str:
        return f"/api/media/covers/{comic_id}"

    @staticmethod
    def original_url(comic_id: str, chapter_id: str, page_index: int) -> str:
        return f"/api/media/comics/{comic_id}/chapters/{chapter_id}/pages/{page_index}/original"
