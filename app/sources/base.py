from __future__ import annotations

from typing import Literal, Protocol

from app.domain.comic import (
    ComicCategory,
    ComicDetail,
    ComicListPage,
    HomeFeed,
    SourceChapterManifest,
)

ComicOrder = Literal["latest", "rating", "views"]


class ComicSource(Protocol):
    async def home(self) -> HomeFeed: ...

    async def latest(self, page: int) -> ComicListPage: ...

    async def search(self, query: str, page: int) -> ComicListPage: ...

    async def categories(self) -> list[ComicCategory]: ...

    async def category(self, category_id: str, page: int, order: ComicOrder) -> ComicListPage: ...

    async def ranking(self, page: int) -> ComicListPage: ...

    async def detail(self, comic_id: str) -> ComicDetail: ...

    async def chapter(self, comic_id: str, chapter_id: str) -> SourceChapterManifest: ...

    async def fetch_media(self, source_url: str) -> tuple[bytes, str]: ...

    async def aclose(self) -> None: ...
