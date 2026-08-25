from __future__ import annotations

import io
import time
from pathlib import Path

from fastapi.testclient import TestClient
from PIL import Image

from app.config import AppConfig
from app.domain.comic import (
    ComicCategory,
    ComicChapter,
    ComicCreatorArchive,
    ComicDetail,
    ComicListPage,
    ComicMetadataItem,
    ComicSummary,
    FeaturedComic,
    HomeFeed,
    SourceChapterManifest,
    SourcePage,
)
from app.main import create_app
from app.sources.base import ComicCreatorKind, ComicOrder
from app.translation.models import TextBlock
from app.translation.pipeline import OCROutput, RenderOutput, TranslationOutput


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

    async def creator(
        self, kind: ComicCreatorKind, creator_id: str, page: int
    ) -> ComicCreatorArchive:
        self.calls.append(("creator", kind, creator_id, page))
        return ComicCreatorArchive(
            kind=kind,
            creator_id=creator_id,
            label="Author One" if kind == "author" else "Artist One",
            result=self._list(page),
        )

    async def ranking(self, page: int) -> ComicListPage:
        self.calls.append(("ranking", page))
        return self._list(page)

    async def detail(self, comic_id: str) -> ComicDetail:
        self.calls.append(("detail", comic_id))
        return ComicDetail(
            comic_id=comic_id,
            title="Alpha Comic",
            cover_url="https://manga18fx.com/webtoon/alpha.jpg",
            release_label="2025",
            authors=[ComicMetadataItem(label="Author One", slug="author-one")],
            artists=[ComicMetadataItem(label="Artist One", slug="artist-one")],
            genres=[ComicMetadataItem(label="School Life", slug="school-life")],
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


class FastPipeline:
    async def run_ocr(self, original_bytes: bytes) -> OCROutput:
        with Image.open(io.BytesIO(original_bytes)) as opened:
            image = opened.convert("RGB")
        return OCROutput(
            image=image,
            sanitized_bytes=original_bytes,
            payload={"result": {}},
            blocks=[TextBlock("Hello", (0, 0, 1, 1))],
            segment_count=1,
        )

    async def translate_blocks(self, blocks: list[TextBlock]) -> TranslationOutput:
        for block in blocks:
            block.translation = "你好"
        return TranslationOutput(blocks=blocks, translated_count=len(blocks))

    async def render(self, image: Image.Image, _blocks: list[TextBlock]) -> RenderOutput:
        rendered = image.copy()
        rendered.putpixel((0, 0), (1, 2, 3))
        buffer = io.BytesIO()
        rendered.save(buffer, format="PNG")
        rendered_bytes = buffer.getvalue()
        return RenderOutput(
            translated_bytes=rendered_bytes,
            width=rendered.width,
            height=rendered.height,
            display_parts=[rendered_bytes],
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
        creator = api_client.get("/api/comics/creators/author/author-one", params={"page": 2})
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
    assert creator.json() == {
        "kind": "author",
        "creatorId": "author-one",
        "label": "Author One",
        "result": {
            "items": [
                {
                    "comicId": "alpha-comic",
                    "title": "Alpha Comic",
                    "coverUrl": "/api/media/covers/alpha-comic",
                    "rating": None,
                    "isAdult": False,
                    "latestChapters": [],
                }
            ],
            "page": 2,
            "availablePages": [],
            "hasPrevious": False,
            "hasNext": False,
        },
    }
    assert detail.json()["coverUrl"] == "/api/media/covers/alpha-comic"
    assert detail.json()["releaseLabel"] == "2025"
    assert detail.json()["authors"] == [{"label": "Author One", "slug": "author-one"}]
    assert detail.json()["artists"] == [{"label": "Artist One", "slug": "artist-one"}]
    assert detail.json()["genres"] == [{"label": "School Life", "slug": "school-life"}]
    assert ("search", "alpha", 2) in source.calls
    assert ("creator", "author", "author-one", 2) in source.calls


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
        invalid_creator_kind = api_client.get("/api/comics/creators/writer/author-one")
        invalid_creator_page = api_client.get(
            "/api/comics/creators/author/author-one", params={"page": 0}
        )

    assert empty_query.status_code == 422
    assert invalid_order.status_code == 422
    assert invalid_page.status_code == 422
    assert invalid_creator_kind.status_code == 422
    assert invalid_creator_page.status_code == 422
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


def test_translation_api_polls_manifest_and_serves_immutable_version(
    tmp_path: Path,
) -> None:
    api_client, _source = catalog_client(tmp_path)

    with api_client:
        manager = api_client.app.state.translation_manager
        manager._pipeline_factory = lambda _semantic, _runtime: FastPipeline()
        configured = api_client.patch(
            "/api/settings",
            json={
                "ocrApiUrl": "https://ocr.example/api",
                "ocrToken": {
                    "action": "replace",
                    "value": "test-ocr-token",
                },
                "translationService": "deeplx",
                "deeplxUrl": {
                    "action": "replace",
                    "value": "https://translate.example/api",
                },
            },
        )
        assert configured.status_code == 200

        initial = api_client.get("/api/comics/alpha-comic/chapters/chapter-12/translation")
        started = api_client.post("/api/comics/alpha-comic/chapters/chapter-12/translation/start")
        deadline = time.monotonic() + 3
        state = None
        while time.monotonic() < deadline:
            state = api_client.get("/api/comics/alpha-comic/chapters/chapter-12/translation")
            if state.json()["status"] == "completed":
                break
            time.sleep(0.01)
        manifest = api_client.get("/api/comics/alpha-comic/chapters/chapter-12/manifest")
        translated_url = manifest.json()["pages"][0]["translatedUrl"]
        translated_part_urls = manifest.json()["pages"][0]["translatedPartUrls"]
        translation_layers = manifest.json()["pages"][0]["translationLayers"]
        translated_segment = api_client.get(translation_layers[0]["url"])
        wrong_version = api_client.get(
            "/api/media/comics/alpha-comic/chapters/chapter-12/pages/0/translated",
            params={"v": "0000000000000000"},
        )
        unconfirmed = api_client.post(
            "/api/comics/alpha-comic/chapters/chapter-12/translation/retranslate",
            json={"confirmed": False},
        )

    assert initial.json()["status"] == "idle"
    assert started.status_code == 200
    assert state is not None and state.json()["status"] == "completed"
    assert state.json()["pages"][0]["translatedUrl"] is None
    assert state.json()["pages"][0]["translatedPartUrls"] == []
    assert translated_url is None
    assert translated_part_urls == []
    assert len(translation_layers) == 1
    assert translation_layers[0]["kind"] == "segment"
    assert "?v=" in translation_layers[0]["url"]
    assert translated_segment.status_code == 200
    assert translated_segment.headers["cache-control"].endswith("immutable")
    assert wrong_version.status_code == 404
    assert unconfirmed.status_code == 422


def test_retry_failed_translation_api_is_camel_case_idempotent_and_safe(
    tmp_path: Path,
) -> None:
    api_client, _source = catalog_client(tmp_path)

    with api_client:
        missing = api_client.post(
            "/api/comics/alpha-comic/chapters/chapter-12/translation/retry-failed"
        )
        manager = api_client.app.state.translation_manager
        repository = api_client.app.state.translation_repository
        manager._ensure_worker = lambda _comic_id, _chapter_id: None
        generation_id = repository.create_generation(
            "alpha-comic",
            "chapter-12",
            semantic_fingerprint="retry-failed-api",
            semantic_settings={"pipelineVersion": "progressive-segment-v1"},
            page_indexes=[0],
            source_pages={0: "https://img.example/0.png"},
            kind="normal",
            progressive=True,
        )
        repository.save_prepared_page(
            generation_id,
            0,
            source_url="https://img.example/0.png",
            original_path="original/0.png",
            original_checksum="checksum",
            width=120,
            height=180,
        )
        repository.commit_segment_plan(
            generation_id,
            [
                {
                    "page_index": 0,
                    "segment_index": 0,
                    "global_index": 0,
                    "source_width": 120,
                    "source_height": 180,
                    "display_top": 0,
                    "display_bottom": 180,
                    "ocr_top": 0,
                    "ocr_bottom": 180,
                    "ocr_input_path": "input/0.png",
                }
            ],
        )
        repository.fail_segment(
            generation_id,
            0,
            0,
            stage="ocr",
            code="OCR_TIMEOUT",
            summary="simulated failure",
        )
        repository.finalize_page_from_segments(generation_id, 0)
        repository.set_generation_status(generation_id, "completed_with_errors")

        retried = api_client.post(
            "/api/comics/alpha-comic/chapters/chapter-12/translation/retry-failed"
        )
        repeated = api_client.post(
            "/api/comics/alpha-comic/chapters/chapter-12/translation/retry-failed"
        )

        repository.fail_segment(
            generation_id,
            0,
            0,
            stage="ocr",
            code="OCR_TIMEOUT",
            summary="simulated failure",
        )
        repository.set_generation_status(
            generation_id,
            "stopping_after_segment",
            stop_requested=True,
        )
        stopping = api_client.post(
            "/api/comics/alpha-comic/chapters/chapter-12/translation/retry-failed"
        )

    assert missing.status_code == 404
    assert missing.json()["code"] == "TRANSLATION_NOT_FOUND"
    assert retried.status_code == 200
    assert retried.json()["retriedCount"] == 1
    assert retried.json()["task"]["generationId"] == generation_id
    assert set(retried.json()) == {"task", "retriedCount"}
    assert "ocrJobId" not in retried.text
    assert "cachePaths" not in retried.text
    assert repeated.status_code == 200
    assert repeated.json()["retriedCount"] == 0
    assert stopping.status_code == 409
    assert stopping.json()["code"] == "TRANSLATION_STOPPING"


def test_background_translation_api_lists_all_chapters_and_force_stops_idempotently(
    tmp_path: Path,
) -> None:
    api_client, source = catalog_client(tmp_path)
    history_payload = {
        "title": "Alpha Comic",
        "chapterId": "chapter-12",
        "chapterTitle": "Chapter 12",
        "pageIndex": 0,
        "totalPages": 1,
    }

    with api_client:
        history = api_client.put("/api/history/alpha-comic", json=history_payload)
        assert history.status_code == 200
        repository = api_client.app.state.translation_repository
        for kind in ("normal", "retranslate"):
            repository.create_generation(
                "alpha-comic",
                "chapter-12",
                semantic_fingerprint=f"alpha-{kind}",
                semantic_settings={"pipelineVersion": "progressive-segment-v1"},
                page_indexes=[0],
                source_pages={0: "https://img.example/alpha.png"},
                kind=kind,
                progressive=True,
            )
        repository.create_generation(
            "beta-comic",
            "chapter-3",
            semantic_fingerprint="beta-normal",
            semantic_settings={"pipelineVersion": "progressive-segment-v1"},
            page_indexes=[0],
            source_pages={0: "https://img.example/beta.png"},
            kind="normal",
            progressive=True,
        )
        source.calls.clear()

        listed = api_client.get("/api/translations/background")
        stopped = api_client.post(
            "/api/comics/alpha-comic/chapters/chapter-12/translation/force-stop"
        )
        stopped_again = api_client.post(
            "/api/comics/alpha-comic/chapters/chapter-12/translation/force-stop"
        )
        remaining = api_client.get("/api/translations/background")
        beta_stopped = api_client.post(
            "/api/comics/beta-comic/chapters/chapter-3/translation/force-stop"
        )

    assert listed.status_code == 200
    assert len(listed.json()) == 2
    alpha, beta = listed.json()
    assert alpha["comicTitle"] == "Alpha Comic"
    assert alpha["chapterTitle"] == "Chapter 12"
    assert alpha["stage"] == "preparing"
    assert "pages" not in alpha
    assert beta["comicTitle"] == "beta-comic"
    assert beta["chapterTitle"] == "chapter-3"
    assert stopped.json()["stoppedGenerations"] == 2
    assert stopped_again.json()["stoppedGenerations"] == 0
    assert [task["comicId"] for task in remaining.json()] == ["beta-comic"]
    assert beta_stopped.json()["stoppedGenerations"] == 1
    assert source.calls == []
