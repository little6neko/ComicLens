from __future__ import annotations

import time
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
from app.domain.library import FavoriteItem, HistoryItem
from app.repositories.database import Database


@dataclass(slots=True)
class SourceMediaRegistry:
    database: Database
    covers: dict[str, str] = field(default_factory=dict)
    pages: dict[tuple[str, str, int], str] = field(default_factory=dict)

    def __post_init__(self) -> None:
        for row in self.database.fetchall(
            "SELECT kind, comic_id, chapter_id, page_index, source_url FROM media_sources"
        ):
            if row["kind"] == "cover":
                self.covers[str(row["comic_id"])] = str(row["source_url"])
            elif row["chapter_id"] is not None and row["page_index"] is not None:
                self.pages[
                    (
                        str(row["comic_id"]),
                        str(row["chapter_id"]),
                        int(row["page_index"]),
                    )
                ] = str(row["source_url"])

    def localize_summary(self, item: ComicSummary) -> ComicSummary:
        if item.cover_url:
            self._register_cover(item.comic_id, item.cover_url)
        return item.model_copy(update={"cover_url": self.cover_url(item.comic_id)})

    def localize_list(self, result: ComicListPage) -> ComicListPage:
        return result.model_copy(
            update={"items": [self.localize_summary(item) for item in result.items]}
        )

    def localize_featured(self, item: FeaturedComic) -> FeaturedComic:
        if item.cover_url:
            self._register_cover(item.comic_id, item.cover_url)
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
            self._register_cover(result.comic_id, result.cover_url)
        return result.model_copy(update={"cover_url": self.cover_url(result.comic_id)})

    def localize_manifest(self, result: SourceChapterManifest) -> ChapterManifest:
        pages: list[ReaderPage] = []
        now = int(time.time())
        for page in result.pages:
            self.pages[(result.comic_id, result.chapter_id, page.index)] = page.source_url
            self.database.execute(
                """
                INSERT INTO media_sources(
                    media_key, kind, comic_id, chapter_id, page_index,
                    source_url, updated_at
                ) VALUES (?, 'original', ?, ?, ?, ?, ?)
                ON CONFLICT(media_key) DO UPDATE SET
                    source_url = excluded.source_url,
                    updated_at = excluded.updated_at
                """,
                (
                    self.page_media_key(result.comic_id, result.chapter_id, page.index),
                    result.comic_id,
                    result.chapter_id,
                    page.index,
                    page.source_url,
                    now,
                ),
            )
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

    def localize_favorite(self, item: FavoriteItem) -> FavoriteItem:
        return item.model_copy(update={"comic": self.localize_summary(item.comic)})

    def localize_history(self, item: HistoryItem) -> HistoryItem:
        return item.model_copy(update={"comic": self.localize_summary(item.comic)})

    def cover_source(self, comic_id: str) -> str | None:
        return self.covers.get(comic_id)

    def _register_cover(self, comic_id: str, source_url: str) -> None:
        self.covers[comic_id] = source_url
        self.database.execute(
            """
            INSERT INTO media_sources(
                media_key, kind, comic_id, source_url, updated_at
            ) VALUES (?, 'cover', ?, ?, ?)
            ON CONFLICT(media_key) DO UPDATE SET
                source_url = excluded.source_url,
                updated_at = excluded.updated_at
            """,
            (self.cover_media_key(comic_id), comic_id, source_url, int(time.time())),
        )

    @staticmethod
    def cover_media_key(comic_id: str) -> str:
        return f"cover:{comic_id}"

    @staticmethod
    def page_media_key(comic_id: str, chapter_id: str, page_index: int) -> str:
        return f"original:{comic_id}:{chapter_id}:{page_index}"

    @staticmethod
    def cover_url(comic_id: str) -> str:
        return f"/api/media/covers/{comic_id}"

    @staticmethod
    def original_url(comic_id: str, chapter_id: str, page_index: int) -> str:
        return f"/api/media/comics/{comic_id}/chapters/{chapter_id}/pages/{page_index}/original"
