from __future__ import annotations

import io
from pathlib import Path

from fastapi.testclient import TestClient
from PIL import Image

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
        buffer = io.BytesIO()
        Image.new("RGB", (1, 1), "white").save(buffer, format="PNG")
        return buffer.getvalue(), "image/png"

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
        cached_page = api_client.get(
            "/api/media/comics/alpha-comic/chapters/chapter-12/pages/0/original"
        )
        cache_stats = api_client.get("/api/system/cache")

    assert before_manifest.status_code == 404
    assert manifest.status_code == 200
    assert manifest.json()["pages"][0]["originalUrl"].endswith("/0/original")
    assert page.status_code == 200
    assert page.content.startswith(b"\x89PNG")
    assert page.headers["content-type"] == "image/png"
    assert cached_page.content == page.content
    assert cached_page.headers["etag"] == page.headers["etag"]
    assert cache_stats.json()["entryCount"] == 1
    assert source.calls.count(("media", "https://img01.manga18fx.com/online/1/12/1.jpg")) == 1


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


def test_library_snapshots_and_read_state_survive_restart(tmp_path: Path) -> None:
    api_client, source = catalog_client(tmp_path)
    config = api_client.app.state.config
    favorite_payload = {
        "title": "Alpha Comic",
        "rating": 4.8,
        "isAdult": False,
        "latestChapters": [
            {
                "chapterId": "chapter-12",
                "title": "Chapter 12",
                "updatedLabel": "Today",
            }
        ],
    }
    history_payload = {
        **favorite_payload,
        "chapterId": "chapter-12",
        "chapterTitle": "Chapter 12",
        "pageIndex": 3,
        "totalPages": 10,
    }

    with api_client:
        favorite = api_client.put("/api/favorites/alpha-comic", json=favorite_payload)
        favorite_update = api_client.put(
            "/api/favorites/alpha-comic",
            json={**favorite_payload, "title": "Alpha Comic Updated"},
        )
        history = api_client.put("/api/history/alpha-comic", json=history_payload)
        read_first = api_client.put(
            "/api/comics/alpha-comic/read-chapters/chapter-12", json={"read": True}
        )
        read_second = api_client.put(
            "/api/comics/alpha-comic/read-chapters/chapter-11", json={"read": True}
        )
        unread_first = api_client.put(
            "/api/comics/alpha-comic/read-chapters/chapter-12", json={"read": False}
        )

    assert favorite.status_code == 200
    assert favorite_update.status_code == 200
    assert favorite_update.json()["favoritedAt"] == favorite.json()["favoritedAt"]
    assert history.status_code == 200
    assert history.json()["pageIndex"] == 3
    assert read_first.json()["chapterIds"] == ["chapter-12"]
    assert set(read_second.json()["chapterIds"]) == {"chapter-11", "chapter-12"}
    assert unread_first.json()["chapterIds"] == ["chapter-11"]
    assert source.calls.count(("detail", "alpha-comic")) == 1

    restarted_source = FakeComicSource()
    with TestClient(create_app(config, comic_source=restarted_source)) as restarted_client:
        favorites = restarted_client.get("/api/favorites")
        histories = restarted_client.get("/api/history")
        read_state = restarted_client.get("/api/comics/alpha-comic/read-chapters")
        cover = restarted_client.get("/api/media/covers/alpha-comic")
        delete_history = restarted_client.delete("/api/history/alpha-comic")
        favorites_after_history_delete = restarted_client.get("/api/favorites")

    assert favorites.json()[0]["comic"]["title"] == "Alpha Comic Updated"
    assert favorites.json()[0]["comic"]["coverUrl"] == ("/api/media/covers/alpha-comic")
    assert histories.json()[0]["chapterId"] == "chapter-12"
    assert histories.json()[0]["comic"]["title"] == "Alpha Comic"
    assert read_state.json()["chapterIds"] == ["chapter-11"]
    assert cover.status_code == 200
    assert ("detail", "alpha-comic") not in restarted_source.calls
    assert delete_history.status_code == 204
    assert len(favorites_after_history_delete.json()) == 1


def test_history_rejects_progress_outside_chapter(tmp_path: Path) -> None:
    api_client, source = catalog_client(tmp_path)

    with api_client:
        invalid = api_client.put(
            "/api/history/alpha-comic",
            json={
                "title": "Alpha Comic",
                "chapterId": "chapter-12",
                "chapterTitle": "Chapter 12",
                "pageIndex": 10,
                "totalPages": 10,
            },
        )

    assert invalid.status_code == 422
    assert source.calls == []
