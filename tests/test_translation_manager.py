from __future__ import annotations

import asyncio
import io
from dataclasses import dataclass
from pathlib import Path

import httpx
import pytest
from PIL import Image

from app.application.settings import SettingsService
from app.cache.storage import MediaCache
from app.config import AppConfig
from app.domain.comic import SourceChapterManifest, SourcePage
from app.media.registry import SourceMediaRegistry
from app.repositories.database import Database
from app.repositories.translation import TranslationRepository
from app.security.secrets import SecretCipher
from app.translation.manager import TranslationManager
from app.translation.models import TextBlock
from app.translation.pipeline import OCROutput, RenderOutput, TranslationOutput
from app.translation.translator import (
    DeepLAuthenticationError,
    DeepLClient,
    DeepLQuotaExceededError,
    DeepLRateLimitError,
    DeepLXClient,
    TranslationInputTooLargeError,
    TranslationProtocolError,
)


def make_png(
    color: tuple[int, int, int],
    size: tuple[int, int] = (120, 180),
) -> bytes:
    buffer = io.BytesIO()
    Image.new("RGB", size, color).save(buffer, format="PNG")
    return buffer.getvalue()


class FakeTranslationSource:
    def __init__(
        self,
        page_count: int = 3,
        image_size: tuple[int, int] = (120, 180),
    ) -> None:
        self.page_count = page_count
        self.image_size = image_size
        self.media_calls: list[str] = []
        self.replacement_urls: dict[int, str] = {}
        self.expired_urls: set[str] = set()

    async def chapter(self, comic_id: str, chapter_id: str) -> SourceChapterManifest:
        return SourceChapterManifest(
            comic_id=comic_id,
            chapter_id=chapter_id,
            title="Chapter 1",
            pages=[
                SourcePage(
                    index=index,
                    source_url=self.replacement_urls.get(
                        index,
                        f"https://img01.manga18fx.com/{index}.png",
                    ),
                )
                for index in range(self.page_count)
            ],
        )

    async def fetch_media(self, source_url: str) -> tuple[bytes, str]:
        self.media_calls.append(source_url)
        if source_url in self.expired_urls:
            raise httpx.HTTPStatusError(
                "expired",
                request=httpx.Request("GET", source_url),
                response=httpx.Response(403),
            )
        index = int(source_url.rsplit("/", 1)[1].split(".", 1)[0])
        return make_png((30 + index, 60, 90), self.image_size), "image/png"

    async def aclose(self) -> None:
        return None


class ControlledPipeline:
    def __init__(self) -> None:
        self.ocr_calls = 0
        self.translation_calls = 0
        self.render_calls = 0
        self.block_first_ocr = False
        self.block_next_render = False
        self.fail_translation_calls: set[int] = set()
        self.ocr_started = asyncio.Event()
        self.ocr_release = asyncio.Event()
        self.render_started = asyncio.Event()
        self.render_release = asyncio.Event()

    async def run_ocr(self, original_bytes: bytes) -> OCROutput:
        self.ocr_calls += 1
        if self.block_first_ocr and self.ocr_calls == 1:
            self.ocr_started.set()
            await self.ocr_release.wait()
        with Image.open(io.BytesIO(original_bytes)) as opened:
            image = opened.convert("RGB")
        return OCROutput(
            image=image,
            sanitized_bytes=original_bytes,
            payload={"call": self.ocr_calls},
            blocks=[TextBlock(text=f"text-{self.ocr_calls}", bbox=(10, 10, 90, 60))],
            segment_count=1,
        )

    async def translate_blocks(self, blocks: list[TextBlock]) -> TranslationOutput:
        self.translation_calls += 1
        if self.translation_calls in self.fail_translation_calls:
            raise TimeoutError("simulated translation timeout")
        for block in blocks:
            block.translation = f"zh-{block.text}"
        return TranslationOutput(blocks=blocks, translated_count=len(blocks))

    async def render(self, image: Image.Image, _blocks: list[TextBlock]) -> RenderOutput:
        self.render_calls += 1
        if self.block_next_render:
            self.block_next_render = False
            self.render_started.set()
            await self.render_release.wait()
        rendered = image.copy()
        rendered.putpixel((0, 0), (self.render_calls % 255, 1, 1))
        buffer = io.BytesIO()
        rendered.save(buffer, format="PNG")
        return RenderOutput(
            translated_bytes=buffer.getvalue(),
            width=rendered.width,
            height=rendered.height,
            display_parts=[],
        )


@dataclass(slots=True)
class ManagerHarness:
    manager: TranslationManager
    repository: TranslationRepository
    cache: MediaCache
    source: FakeTranslationSource
    pipeline: ControlledPipeline
    database: Database

    async def close(self) -> None:
        await self.manager.shutdown()
        self.database.close()


def create_harness(
    tmp_path: Path,
    *,
    page_count: int = 3,
    image_size: tuple[int, int] = (120, 180),
    translation_settings: dict[str, object] | None = None,
) -> ManagerHarness:
    initial_settings: dict[str, object] = {
        "ocr_api_url": "https://ocr.example/api",
        "ocr_token": "test-ocr-token",
        "translation_service": "deeplx",
        "deeplx_url": "https://translate.example/api",
    }
    initial_settings.update(translation_settings or {})
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
        initial_settings=initial_settings,
    )
    config.ensure_directories()
    database = Database(config.database_path)
    cipher = SecretCipher(config.secrets_path, database)
    settings = SettingsService(database, cipher, config)
    source = FakeTranslationSource(page_count, image_size)
    registry = SourceMediaRegistry(database)
    cache = MediaCache(config.cache_dir, database, 5120 * 1024 * 1024)
    repository = TranslationRepository(database)
    pipeline = ControlledPipeline()
    manager = TranslationManager(
        repository=repository,
        cache=cache,
        source=source,
        registry=registry,
        settings=settings,
        pipeline_factory=lambda _semantic, _runtime: pipeline,  # type: ignore[arg-type]
    )
    return ManagerHarness(manager, repository, cache, source, pipeline, database)


async def wait_for(predicate, timeout: float = 3.0) -> None:
    async def poll() -> None:
        while not predicate():  # noqa: ASYNC110 - polling persisted task state is intentional
            await asyncio.sleep(0.01)

    await asyncio.wait_for(poll(), timeout)


def test_repository_commits_segment_plan_and_publishes_atomic_layer(tmp_path: Path) -> None:
    harness = create_harness(tmp_path, page_count=1)
    try:
        generation_id = harness.repository.create_generation(
            "alpha",
            "chapter-1",
            semantic_fingerprint="segments",
            semantic_settings={"pipelineVersion": "progressive-segment-v1"},
            page_indexes=[0],
            source_pages={0: "https://img.example/0.png"},
            kind="normal",
            progressive=True,
        )
        harness.repository.save_prepared_page(
            generation_id,
            0,
            source_url="https://img.example/0.png",
            original_path="chapters/original.img",
            original_checksum="checksum",
            width=120,
            height=180,
        )
        harness.repository.commit_segment_plan(
            generation_id,
            [
                {
                    "page_index": 0,
                    "segment_index": 0,
                    "global_index": 0,
                    "source_width": 120,
                    "source_height": 180,
                    "display_top": 0,
                    "display_bottom": 90,
                    "ocr_top": 0,
                    "ocr_bottom": 100,
                    "ocr_input_path": "segments/0.png",
                },
                {
                    "page_index": 0,
                    "segment_index": 1,
                    "global_index": 1,
                    "source_width": 120,
                    "source_height": 180,
                    "display_top": 90,
                    "display_bottom": 180,
                    "ocr_top": 80,
                    "ocr_bottom": 180,
                    "ocr_input_path": "segments/1.png",
                },
            ],
        )

        planned = harness.repository.task_state("alpha", "chapter-1", generation_id)
        assert planned.status == "queued"
        assert planned.planning_complete is True
        assert planned.total_segments == 2
        assert [(item.display_top, item.display_bottom) for item in planned.pages[0].segments] == [
            (0, 90),
            (90, 180),
        ]

        media = harness.cache.put_bytes(
            bundle_key="chapter:test",
            bundle_kind="chapter",
            comic_id="alpha",
            chapter_id="chapter-1",
            relative_path="segments/translated-0.png",
            entry_kind="translated_segment",
            content=make_png((10, 20, 30)),
            media_type="image/png",
            verify_image=True,
        )
        harness.repository.complete_segment(
            generation_id,
            "alpha",
            "chapter-1",
            0,
            0,
            translated_path="segments/translated-0.png",
            translated_version=media.etag,
        )

        published = harness.repository.task_state("alpha", "chapter-1", generation_id)
        assert published.completed_segments == 1
        assert published.failed_segments == 0
        assert published.pages[0].segments[0].translated_version == media.etag
        assert published.pages[0].translation_layers[0].kind == "segment"
        assert published.pages[0].translation_layers[0].top == 0
        assert published.pages[0].translation_layers[0].bottom == 90
    finally:
        harness.database.close()


def test_background_tasks_and_force_pause_preserve_segment_checkpoints(tmp_path: Path) -> None:
    harness = create_harness(tmp_path, page_count=1)
    try:
        generation_id = harness.repository.create_generation(
            "alpha",
            "chapter-1",
            semantic_fingerprint="force-stop-checkpoints",
            semantic_settings={"pipelineVersion": "progressive-segment-v1"},
            page_indexes=[0],
            source_pages={0: "https://img.example/0.png"},
            kind="normal",
            progressive=True,
        )
        harness.repository.save_prepared_page(
            generation_id,
            0,
            source_url="https://img.example/0.png",
            original_path="chapters/original.png",
            original_checksum="checksum",
            width=120,
            height=180,
        )
        harness.repository.commit_segment_plan(
            generation_id,
            [
                {
                    "page_index": 0,
                    "segment_index": segment_index,
                    "global_index": segment_index,
                    "source_width": 120,
                    "source_height": 180,
                    "display_top": segment_index * 90,
                    "display_bottom": (segment_index + 1) * 90,
                    "ocr_top": max(0, segment_index * 90 - 10),
                    "ocr_bottom": (segment_index + 1) * 90,
                    "ocr_input_path": f"segments/input-{segment_index}.png",
                }
                for segment_index in range(2)
            ],
        )
        translated = harness.cache.put_bytes(
            bundle_key=harness.cache.ensure_chapter_bundle("alpha", "chapter-1"),
            bundle_kind="chapter",
            comic_id="alpha",
            chapter_id="chapter-1",
            relative_path="segments/translated-0.png",
            entry_kind="translated_segment",
            content=make_png((10, 20, 30)),
            media_type="image/png",
            verify_image=True,
        )
        harness.repository.complete_segment(
            generation_id,
            "alpha",
            "chapter-1",
            0,
            0,
            translated_path="segments/translated-0.png",
            translated_version=translated.etag,
        )
        harness.repository.set_segment_stage(
            generation_id,
            0,
            1,
            "ocr",
            increment_attempts=True,
            paths={
                "ocr_path": "segments/ocr-1.json",
                "blocks_path": "segments/blocks-1.json",
                "translations_path": "segments/translations-1.json",
                "translated_path": "segments/rendered-1.png",
            },
            job_id="paddle-job-1",
        )
        harness.repository.set_page_stage(generation_id, 0, "rendering")
        harness.repository.set_generation_status(
            generation_id,
            "running",
            current_page_index=0,
            current_segment_index=1,
        )
        harness.database.execute(
            """
            INSERT INTO reading_history(
                comic_id, title, cover_source_url, rating, is_adult,
                latest_chapters_json, chapter_id, chapter_title,
                page_index, total_pages, updated_at
            ) VALUES (?, ?, ?, NULL, 0, '[]', ?, ?, 0, 1, ?)
            """,
            ("alpha", "Alpha Comic", "https://img.example/cover.png", "chapter-1", "第 1 话", 1),
        )
        harness.cache.set_chapter_active("alpha", "chapter-1", True)

        tasks = harness.repository.background_tasks()
        assert len(tasks) == 1
        assert tasks[0].comic_title == "Alpha Comic"
        assert tasks[0].chapter_title == "第 1 话"
        assert tasks[0].stage == "ocr"
        assert tasks[0].prepared_pages == 1
        assert tasks[0].completed_segments == 1
        assert tasks[0].total_segments == 2

        assert harness.repository.force_pause_chapter("alpha", "chapter-1") == 1
        assert harness.repository.force_pause_chapter("alpha", "chapter-1") == 0

        state = harness.repository.task_state("alpha", "chapter-1", generation_id)
        assert state.status == "paused"
        assert state.current_page_index is None
        assert state.current_segment is None
        assert state.pages[0].status == "pending"
        assert [segment.status for segment in state.pages[0].segments] == [
            "completed",
            "pending",
        ]
        checkpoint = harness.repository.segment(generation_id, 0, 1)
        assert checkpoint is not None
        assert checkpoint["ocr_job_id"] == "paddle-job-1"
        assert checkpoint["ocr_path"] == "segments/ocr-1.json"
        assert checkpoint["blocks_path"] == "segments/blocks-1.json"
        assert checkpoint["translations_path"] == "segments/translations-1.json"
        assert checkpoint["translated_path"] == "segments/rendered-1.png"
        assert harness.repository.translation_layers("alpha", "chapter-1", 0)[0].segment_index == 0
        bundle = harness.database.fetchone(
            """
            SELECT active_task FROM cache_bundles
            WHERE comic_id = ? AND chapter_id = ?
            """,
            ("alpha", "chapter-1"),
        )
        assert bundle is not None and bundle["active_task"] == 0
        assert harness.repository.background_tasks() == []
    finally:
        harness.database.close()


def test_pause_wins_race_with_preparation_and_running_transitions(tmp_path: Path) -> None:
    harness = create_harness(tmp_path, page_count=1)
    try:
        generation_id = harness.repository.create_generation(
            "alpha",
            "chapter-1",
            semantic_fingerprint="pause-race",
            semantic_settings={"pipelineVersion": "progressive-segment-v1"},
            page_indexes=[0],
            source_pages={0: "https://img.example/0.png"},
            kind="normal",
            progressive=True,
        )
        harness.repository.request_stop(generation_id)
        assert harness.repository.begin_preparing(generation_id) is False

        harness.repository.resume(generation_id)
        harness.repository.commit_segment_plan(
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
                    "ocr_input_path": "segments/0.png",
                }
            ],
        )
        harness.repository.request_stop(generation_id)
        assert harness.repository.begin_running(generation_id) is False
        assert harness.repository.generation(generation_id)["status"] == "paused"
    finally:
        harness.database.close()


@pytest.mark.asyncio
async def test_pause_finishes_current_segment_then_resumes_next(
    tmp_path: Path,
) -> None:
    harness = create_harness(tmp_path)
    harness.pipeline.block_first_ocr = True
    try:
        started = await harness.manager.start("alpha", "chapter-1")
        await wait_for(harness.pipeline.ocr_started.is_set)
        during_ocr = harness.manager.state("alpha", "chapter-1")
        assert len(harness.source.media_calls) == 3
        assert during_ocr.total_segments == 3
        assert during_ocr.completed_segments == 0

        stopping = await harness.manager.pause("alpha", "chapter-1")
        assert started.generation_id == stopping.generation_id
        assert stopping.status == "stopping_after_segment"
        assert stopping.current_page_index == 0
        assert stopping.current_segment is not None
        assert stopping.current_segment.segment_index == 0

        harness.pipeline.ocr_release.set()
        await wait_for(lambda: harness.manager.state("alpha", "chapter-1").status == "paused")
        paused = harness.manager.state("alpha", "chapter-1")
        assert paused.completed_pages == 1
        assert [page.status for page in paused.pages] == [
            "completed",
            "pending",
            "pending",
        ]
        assert harness.pipeline.ocr_calls == 1

        resumed = await harness.manager.start("alpha", "chapter-1")
        assert resumed.generation_id == started.generation_id
        await wait_for(lambda: harness.manager.state("alpha", "chapter-1").status == "completed")
        finished = harness.manager.state("alpha", "chapter-1")
        assert finished.completed_pages == 3
        assert harness.pipeline.ocr_calls == 3
    finally:
        await harness.close()


@pytest.mark.asyncio
async def test_force_stop_cancels_current_segment_immediately_and_can_resume(
    tmp_path: Path,
) -> None:
    harness = create_harness(tmp_path, page_count=2)
    harness.pipeline.block_first_ocr = True
    try:
        started = await harness.manager.start("alpha", "chapter-1")
        await wait_for(harness.pipeline.ocr_started.is_set)

        result = await harness.manager.force_stop("alpha", "chapter-1")
        paused = harness.manager.state("alpha", "chapter-1")

        assert result.stopped_generations == 1
        assert paused.generation_id == started.generation_id
        assert paused.status == "paused"
        assert paused.current_segment is None
        assert paused.pages[0].segments[0].status == "pending"
        assert harness.pipeline.ocr_calls == 1
        assert harness.pipeline.translation_calls == 0
        assert harness.pipeline.render_calls == 0
        assert harness.manager.background_tasks() == []

        resumed = await harness.manager.start("alpha", "chapter-1")
        assert resumed.generation_id == started.generation_id
        await wait_for(lambda: harness.manager.state("alpha", "chapter-1").status == "completed")
        assert harness.pipeline.ocr_calls == 3
        assert harness.pipeline.translation_calls == 2
        assert harness.pipeline.render_calls == 2
    finally:
        await harness.close()


@pytest.mark.asyncio
async def test_force_stop_does_not_cancel_another_chapter_worker(tmp_path: Path) -> None:
    harness = create_harness(tmp_path, page_count=1)
    harness.pipeline.block_first_ocr = True
    try:
        await harness.manager.start("alpha", "chapter-1")
        await wait_for(harness.pipeline.ocr_started.is_set)
        await harness.manager.start("beta", "chapter-2")

        result = await harness.manager.force_stop("alpha", "chapter-1")
        await wait_for(lambda: harness.manager.state("beta", "chapter-2").status == "completed")

        assert result.stopped_generations == 1
        assert harness.manager.state("alpha", "chapter-1").status == "paused"
        assert harness.manager.state("beta", "chapter-2").status == "completed"
    finally:
        await harness.close()


@pytest.mark.asyncio
async def test_long_page_has_exact_segment_total_before_first_ocr_and_publishes_in_order(
    tmp_path: Path,
) -> None:
    harness = create_harness(
        tmp_path,
        page_count=1,
        image_size=(300, 2400),
        translation_settings={
            "long_image_threshold": 1000,
            "ocr_slice_height": 700,
            "ocr_slice_overlap": 100,
        },
    )
    harness.pipeline.block_first_ocr = True
    try:
        await harness.manager.start("alpha", "chapter-1")
        await wait_for(harness.pipeline.ocr_started.is_set)

        planned = harness.manager.state("alpha", "chapter-1")
        assert planned.planning_complete is True
        assert planned.total_segments == 4
        assert planned.completed_segments == 0
        assert [segment.global_index for segment in planned.pages[0].segments] == [0, 1, 2, 3]
        assert planned.pages[0].segments[-1].display_bottom == 2400

        harness.pipeline.ocr_release.set()
        await wait_for(lambda: harness.manager.state("alpha", "chapter-1").status == "completed")
        completed = harness.manager.state("alpha", "chapter-1")
        assert completed.completed_segments == 4
        assert [layer.segment_index for layer in completed.pages[0].translation_layers] == [
            0,
            1,
            2,
            3,
        ]
        assert harness.pipeline.ocr_calls == 4
    finally:
        await harness.close()


@pytest.mark.asyncio
async def test_preparation_refreshes_an_expired_frozen_source_url(tmp_path: Path) -> None:
    harness = create_harness(tmp_path, page_count=1)
    old_url = "https://img01.manga18fx.com/0.png"
    fresh_url = "https://img02.manga18fx.com/fresh/0.png"
    try:
        await harness.manager._ensure_source_pages("alpha", "chapter-1")
        harness.source.expired_urls.add(old_url)
        harness.source.replacement_urls[0] = fresh_url

        await harness.manager.start("alpha", "chapter-1")
        await wait_for(lambda: harness.manager.state("alpha", "chapter-1").status == "completed")

        state = harness.manager.state("alpha", "chapter-1")
        page = harness.repository.page(str(state.generation_id), 0)
        assert page is not None
        assert page["source_url"] == fresh_url
        assert harness.source.media_calls[:2] == [old_url, fresh_url]
    finally:
        await harness.close()


@pytest.mark.asyncio
async def test_page_failure_continues_and_retry_reuses_ocr_checkpoint(
    tmp_path: Path,
) -> None:
    harness = create_harness(tmp_path, page_count=2)
    harness.pipeline.fail_translation_calls.add(1)
    try:
        await harness.manager.start("alpha", "chapter-1")
        await wait_for(
            lambda: harness.manager.state("alpha", "chapter-1").status == "completed_with_errors"
        )
        failed = harness.manager.state("alpha", "chapter-1")
        assert [page.status for page in failed.pages] == ["failed", "completed"]
        assert failed.pages[0].error is not None
        assert failed.pages[0].error.code == "SEGMENTS_FAILED"
        assert failed.pages[0].segments[0].error is not None
        assert failed.pages[0].segments[0].error.code == "TRANSLATION_TIMEOUT"
        assert harness.pipeline.ocr_calls == 2
        assert harness.pipeline.translation_calls == 2

        await harness.manager.retry_segment("alpha", "chapter-1", 0, 0)
        await wait_for(lambda: harness.manager.state("alpha", "chapter-1").status == "completed")
        retried = harness.manager.state("alpha", "chapter-1")
        assert [page.status for page in retried.pages] == [
            "completed",
            "completed",
        ]
        assert harness.pipeline.ocr_calls == 2
        assert harness.pipeline.translation_calls == 3
        assert retried.pages[0].segments[0].attempts == 2
    finally:
        await harness.close()


@pytest.mark.asyncio
async def test_retranslate_keeps_old_page_until_new_atomic_publish(
    tmp_path: Path,
) -> None:
    harness = create_harness(tmp_path, page_count=1)
    try:
        first = await harness.manager.start("alpha", "chapter-1")
        await wait_for(lambda: harness.manager.state("alpha", "chapter-1").status == "completed")
        first_active = harness.repository.active_page("alpha", "chapter-1", 0)
        assert first_active is None
        first_layers = harness.repository.translation_layers("alpha", "chapter-1", 0)
        assert len(first_layers) == 1
        first_version = first_layers[0].version

        harness.pipeline.block_next_render = True
        second = await harness.manager.retranslate("alpha", "chapter-1")
        duplicate = await harness.manager.retranslate("alpha", "chapter-1")
        assert second.generation_id != first.generation_id
        assert duplicate.generation_id == second.generation_id
        await wait_for(harness.pipeline.render_started.is_set)

        during = harness.repository.translation_layers("alpha", "chapter-1", 0)
        assert [layer.version for layer in during] == [first_version]

        harness.pipeline.render_release.set()
        await wait_for(lambda: harness.manager.state("alpha", "chapter-1").status == "completed")
        after = harness.repository.translation_layers("alpha", "chapter-1", 0)
        assert len(after) == 1
        assert after[0].generation_id == second.generation_id
        assert after[0].version != first_version
    finally:
        await harness.close()


@pytest.mark.asyncio
async def test_interrupted_stage_recovers_to_paused_pending(tmp_path: Path) -> None:
    harness = create_harness(tmp_path, page_count=1)
    generation_id = harness.repository.create_generation(
        "alpha",
        "chapter-1",
        semantic_fingerprint="fingerprint",
        semantic_settings={},
        page_indexes=[0],
        kind="normal",
    )
    harness.repository.set_generation_status(generation_id, "running", current_page_index=0)
    harness.repository.set_page_stage(generation_id, 0, "ocr")
    harness.cache.set_chapter_active("alpha", "chapter-1", True)

    recovered = harness.repository.recover_interrupted()
    state = harness.repository.task_state("alpha", "chapter-1", generation_id)

    assert recovered == 1
    assert state.status == "paused"
    assert state.current_page_index is None
    assert state.pages[0].status == "pending"
    bundle = harness.database.fetchone(
        "SELECT active_task FROM cache_bundles WHERE comic_id = 'alpha'"
    )
    assert bundle is not None and bundle["active_task"] == 0
    await harness.close()


@pytest.mark.asyncio
async def test_manager_selects_only_configured_semantic_translation_service(
    tmp_path: Path,
) -> None:
    harness = create_harness(tmp_path, page_count=1)
    try:
        runtime = harness.manager._runtime_settings(require_services=True)
        deeplx_semantic, deeplx_fingerprint = harness.manager._semantic_settings(
            {0: "https://img.example/0.png"},
            runtime,
        )
        deeplx_pipeline = harness.manager._build_pipeline(deeplx_semantic, runtime)
        assert isinstance(deeplx_pipeline.translator, DeepLXClient)

        runtime.update(
            {
                "translation_service": "deepl",
                "deepl_api_key": "test-key:fx",
                "deeplx_url": "",
            }
        )
        deepl_semantic, deepl_fingerprint = harness.manager._semantic_settings(
            {0: "https://img.example/0.png"},
            runtime,
        )
        deepl_pipeline = harness.manager._build_pipeline(deepl_semantic, runtime)
        assert isinstance(deepl_pipeline.translator, DeepLClient)
        assert deepl_semantic["translationService"] == "deepl"
        assert deepl_semantic["targetLanguage"] == "ZH-HANS"
        assert "ocrMode" not in deepl_semantic
        assert deepl_fingerprint != deeplx_fingerprint
    finally:
        await harness.close()


@pytest.mark.parametrize(
    ("error", "code"),
    [
        (DeepLAuthenticationError("auth"), "DEEPL_AUTH_ERROR"),
        (DeepLQuotaExceededError("quota"), "DEEPL_QUOTA_EXCEEDED"),
        (DeepLRateLimitError("rate"), "DEEPL_RATE_LIMITED"),
        (TranslationInputTooLargeError("large"), "TRANSLATION_INPUT_TOO_LARGE"),
        (TranslationProtocolError("protocol"), "TRANSLATION_PROTOCOL_ERROR"),
        (httpx.ConnectError("network"), "TRANSLATION_NETWORK_ERROR"),
        (httpx.ReadTimeout("timeout"), "TRANSLATION_TIMEOUT"),
    ],
)
def test_manager_classifies_translation_errors(error: Exception, code: str) -> None:
    assert TranslationManager._classify_error("translating", error)[0] == code
