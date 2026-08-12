from __future__ import annotations

from pathlib import Path

from fastapi.testclient import TestClient

from app.config import AppConfig
from app.domain.comic import (
    ComicCategory,
    ComicChapter,
    ComicDetail,
    ComicListPage,
    ComicSummary,
    FeaturedComic,
    HomeFeed,
    SourceChapterManifest,
    SourcePage,
)
from app.main import create_app
from app.sources.base import ComicOrder


class FakeComicSource:
    def __init__(self) -> None:
        self.calls: list[tuple[object, ...]] = []

    async def home(self) -> HomeFeed:
        return HomeFeed(
            featured=[
                FeaturedComic(
                    comic_id="alpha-comic",
                    title="Alpha Comic",
                    cover_url="https://manga18fx.com/webtoon/alpha.jpg",
                    chapter_label="Chapter 12",
                )
            ],
            latest=self._list(1),
        )

    async def latest(self, page: int) -> ComicListPage:
        self.calls.append(("latest", page))
        return self._list(page)

    async def search(self, query: str, page: int) -> ComicListPage:
        self.calls.append(("search", query, page))
        return self._list(page)

    async def categories(self) -> list[ComicCategory]:
        return [ComicCategory(category_id="action", label="Action")]

    async def category(self, category_id: str, page: int, order: ComicOrder) -> ComicListPage:
        self.calls.append(("category", category_id, page, order))
        return self._list(page)

    async def ranking(self, page: int) -> ComicListPage:
        self.calls.append(("ranking", page))
        return self._list(page)

    async def detail(self, comic_id: str) -> ComicDetail:
        self.calls.append(("detail", comic_id))
        return ComicDetail(
            comic_id=comic_id,
            title="Alpha Comic",
            cover_url="https://manga18fx.com/webtoon/alpha.jpg",
            chapters=[ComicChapter(chapter_id="chapter-12", title="Chapter 12")],
        )

    async def chapter(self, comic_id: str, chapter_id: str) -> SourceChapterManifest:
        self.calls.append(("chapter", comic_id, chapter_id))
        return SourceChapterManifest(
            comic_id=comic_id,
            chapter_id=chapter_id,
            title="Chapter 12",
            pages=[
                SourcePage(
                    index=0,
                    source_url="https://img01.manga18fx.com/online/1/12/1.jpg",
                )
            ],
        )

    async def fetch_media(self, source_url: str) -> tuple[bytes, str]:
        self.calls.append(("media", source_url))
        return b"image", "image/jpeg"

    async def aclose(self) -> None:
        return None

    @staticmethod
    def _list(page: int) -> ComicListPage:
        return ComicListPage(
            items=[
                ComicSummary(
                    comic_id="alpha-comic",
                    title="Alpha Comic",
                    cover_url="https://manga18fx.com/webtoon/alpha.jpg",
                )
            ],
            page=page,
        )


def catalog_client(tmp_path: Path) -> tuple[TestClient, FakeComicSource]:
    config = AppConfig(
        app_name="ComicLens",
        host="0.0.0.0",
        port=8233,
        data_dir=tmp_path / "data",
        static_dir=tmp_path / "static",
        cache_max_mb=5120,
        access_password=None,
        upstream_base_url="https://manga18fx.com",
        request_timeout=30,
        log_level="INFO",
    )
    source = FakeComicSource()
    return TestClient(create_app(config, comic_source=source)), source


def test_catalog_api_uses_camel_case_and_controlled_media_urls(tmp_path: Path) -> None:
    api_client, source = catalog_client(tmp_path)

    with api_client:
        search = api_client.get("/api/comics/search", params={"q": "alpha", "page": 2})
        ranking = api_client.get("/api/comics/ranking", params={"page": 3})
        detail = api_client.get("/api/comics/alpha-comic")

    assert search.status_code == 200
    assert search.json()["items"][0] == {
        "comicId": "alpha-comic",
        "title": "Alpha Comic",
        "coverUrl": "/api/media/covers/alpha-comic",
        "rating": None,
        "isAdult": False,
        "latestChapters": [],
    }
    assert ranking.json()["period"] == "week"
    assert ranking.json()["result"]["page"] == 3
    assert detail.json()["coverUrl"] == "/api/media/covers/alpha-comic"
    assert ("search", "alpha", 2) in source.calls


def test_manifest_registers_only_discovered_page_media(tmp_path: Path) -> None:
    api_client, source = catalog_client(tmp_path)

    with api_client:
        before_manifest = api_client.get(
            "/api/media/comics/alpha-comic/chapters/chapter-12/pages/0/original"
        )
        manifest = api_client.get("/api/comics/alpha-comic/chapters/chapter-12/manifest")
        page = api_client.get("/api/media/comics/alpha-comic/chapters/chapter-12/pages/0/original")

    assert before_manifest.status_code == 404
    assert manifest.status_code == 200
    assert manifest.json()["pages"][0]["originalUrl"].endswith("/0/original")
    assert page.status_code == 200
    assert page.content == b"image"
    assert page.headers["content-type"] == "image/jpeg"
    assert ("media", "https://img01.manga18fx.com/online/1/12/1.jpg") in source.calls


def test_catalog_validation_rejects_invalid_parameters_before_source_call(
    tmp_path: Path,
) -> None:
    api_client, source = catalog_client(tmp_path)

    with api_client:
        empty_query = api_client.get("/api/comics/search", params={"q": ""})
        invalid_order = api_client.get("/api/comics/categories/action", params={"order": "random"})
        invalid_page = api_client.get("/api/comics/ranking", params={"page": 0})

    assert empty_query.status_code == 422
    assert invalid_order.status_code == 422
    assert invalid_page.status_code == 422
    assert source.calls == []
