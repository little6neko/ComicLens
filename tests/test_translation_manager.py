from __future__ import annotations

import asyncio
import io
import json
from collections.abc import Callable
from dataclasses import dataclass
from pathlib import Path

import httpx
import pytest
from PIL import Image

from app.application.settings import SettingsService
from app.cache.storage import MediaCache
from app.config import AppConfig
from app.domain.comic import SourceChapterManifest, SourcePage
from app.errors import AppError
from app.media.registry import SourceMediaRegistry
from app.repositories.database import Database
from app.repositories.translation import TranslationRepository
from app.security.secrets import SecretCipher
from app.translation.concurrency import DynamicConcurrencyLimiter
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
        self.block_page_index: int | None = None
        self.blocked_page_started = asyncio.Event()
        self.blocked_page_release = asyncio.Event()

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
        if index == self.block_page_index:
            self.blocked_page_started.set()
            await self.blocked_page_release.wait()
        return make_png((30 + index, 60, 90), self.image_size), "image/png"

    async def aclose(self) -> None:
        return None


class ControlledPipeline:
    def __init__(self) -> None:
        self.ocr_limiter: DynamicConcurrencyLimiter | None = None
        self.ocr_calls = 0
        self.active_ocr_calls = 0
        self.max_active_ocr_calls = 0
        self.completed_ocr_calls: list[int] = []
        self.completed_ocr_segments: list[tuple[int, int]] = []
        self.ocr_job_inputs: list[str | None] = []
        self.submitted_ocr_jobs: list[str] = []
        self.translation_calls = 0
        self.translation_inputs: list[list[str]] = []
        self.render_calls = 0
        self.block_first_ocr = False
        self.block_ocr_call: int | None = None
        self.block_next_render = False
        self.fail_translation_calls: set[int] = set()
        self.fail_ocr_segments: set[tuple[int, int]] = set()
        self.blocked_ocr_calls: set[int] = set()
        self.ocr_call_releases: dict[int, asyncio.Event] = {}
        self.blocked_ocr_segments: set[tuple[int, int]] = set()
        self.ocr_segment_releases: dict[tuple[int, int], asyncio.Event] = {}
        self.ocr_started = asyncio.Event()
        self.ocr_release = asyncio.Event()
        self.render_started = asyncio.Event()
        self.render_release = asyncio.Event()

    async def run_ocr(self, original_bytes: bytes) -> OCROutput:
        if self.ocr_limiter is not None:
            async with self.ocr_limiter.slot():
                return await self._run_ocr(original_bytes)
        return await self._run_ocr(original_bytes)

    async def run_segment_ocr(
        self,
        original_bytes: bytes,
        *,
        job_id: str | None,
        on_job_submitted: Callable[[str], None],
    ) -> OCROutput:
        self.ocr_job_inputs.append(job_id)
        if job_id is None:
            submitted_job_id = f"fake-job-{len(self.submitted_ocr_jobs) + 1}"
            self.submitted_ocr_jobs.append(submitted_job_id)
            on_job_submitted(submitted_job_id)
        return await self.run_ocr(original_bytes)

    async def _run_ocr(self, original_bytes: bytes) -> OCROutput:
        self.ocr_calls += 1
        call_number = self.ocr_calls
        task_name = asyncio.current_task().get_name() if asyncio.current_task() else ""
        name_parts = task_name.rsplit(":", 2)
        segment_key = (
            (int(name_parts[-2]), int(name_parts[-1]))
            if len(name_parts) == 3
            and name_parts[-2].isdigit()
            and name_parts[-1].isdigit()
            else None
        )
        self.active_ocr_calls += 1
        self.max_active_ocr_calls = max(self.max_active_ocr_calls, self.active_ocr_calls)
        try:
            if (
                (self.block_first_ocr and call_number == 1)
                or self.block_ocr_call == call_number
                or call_number in self.blocked_ocr_calls
                or segment_key in self.blocked_ocr_segments
            ):
                self.ocr_started.set()
                if segment_key in self.blocked_ocr_segments:
                    release = self.ocr_segment_releases.setdefault(segment_key, asyncio.Event())
                    await release.wait()
                elif call_number in self.blocked_ocr_calls:
                    release = self.ocr_call_releases.setdefault(call_number, asyncio.Event())
                    await release.wait()
                else:
                    await self.ocr_release.wait()
            with Image.open(io.BytesIO(original_bytes)) as opened:
                image = opened.convert("RGB")
            if segment_key in self.fail_ocr_segments:
                raise TimeoutError("simulated OCR timeout")
            block_top = max(10, image.height - 70)
            self.completed_ocr_calls.append(call_number)
            if segment_key is not None:
                self.completed_ocr_segments.append(segment_key)
            return OCROutput(
                image=image,
                sanitized_bytes=original_bytes,
                payload={"call": call_number},
                blocks=[
                    TextBlock(
                        text=f"text-{call_number}",
                        bbox=(10, block_top, 90, block_top + 50),
                    )
                ],
                segment_count=1,
            )
        finally:
            self.active_ocr_calls -= 1

    async def translate_blocks(self, blocks: list[TextBlock]) -> TranslationOutput:
        self.translation_calls += 1
        self.translation_inputs.append([block.text for block in blocks])
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
    pipeline.ocr_limiter = manager._ocr_limiter
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


def test_repository_appends_prepared_pages_and_grows_segment_total(tmp_path: Path) -> None:
    harness = create_harness(tmp_path, page_count=2)
    try:
        generation_id = harness.repository.create_generation(
            "alpha",
            "chapter-1",
            semantic_fingerprint="streaming-segments",
            semantic_settings={"pipelineVersion": "progressive-segment-v2"},
            page_indexes=[0, 1],
            source_pages={
                0: "https://img.example/0.png",
                1: "https://img.example/1.png",
            },
            kind="normal",
            progressive=True,
        )

        def page_segments(page_index: int) -> list[dict[str, object]]:
            return [
                {
                    "page_index": page_index,
                    "segment_index": segment_index,
                    "source_width": 720,
                    "source_height": 8000,
                    "display_top": segment_index * 1600,
                    "display_bottom": (segment_index + 1) * 1600,
                    "ocr_top": max(0, segment_index * 1600 - 200),
                    "ocr_bottom": (segment_index + 1) * 1600,
                    "ocr_input_path": f"segments/{page_index}-{segment_index}.png",
                }
                for segment_index in range(5)
            ]

        first = page_segments(0)
        assert harness.repository.append_prepared_page_segments(
            generation_id,
            0,
            source_url="https://img.example/0.png",
            original_path="originals/0.png",
            original_checksum="checksum-0",
            width=720,
            height=8000,
            segments=first,
        ) == 5
        assert harness.repository.task_state(
            "alpha", "chapter-1", generation_id
        ).total_segments == 5

        for segment_index in range(3):
            harness.repository.complete_segment(
                generation_id,
                "alpha",
                "chapter-1",
                0,
                segment_index,
                translated_path=f"segments/translated-0-{segment_index}.png",
                translated_version=f"version-{segment_index}",
            )
        partial = harness.repository.task_state("alpha", "chapter-1", generation_id)
        assert (partial.completed_segments, partial.total_segments) == (3, 5)

        assert harness.repository.append_prepared_page_segments(
            generation_id,
            1,
            source_url="https://img.example/1.png",
            original_path="originals/1.png",
            original_checksum="checksum-1",
            width=720,
            height=8000,
            segments=page_segments(1),
        ) == 5
        grown = harness.repository.task_state("alpha", "chapter-1", generation_id)
        assert (grown.completed_segments, grown.total_segments) == (3, 10)
        assert [segment.global_index for page in grown.pages for segment in page.segments] == list(
            range(10)
        )

        assert harness.repository.append_prepared_page_segments(
            generation_id,
            1,
            source_url="https://img.example/1.png",
            original_path="originals/1.png",
            original_checksum="checksum-1",
            width=720,
            height=8000,
            segments=page_segments(1),
        ) == 0
        harness.repository.complete_segment_plan(generation_id)
        completed_plan = harness.repository.task_state("alpha", "chapter-1", generation_id)
        assert completed_plan.planning_complete is True
        assert completed_plan.total_segments == 10
    finally:
        harness.database.close()


def test_repository_claims_and_releases_segment_ocr_checkpoint(tmp_path: Path) -> None:
    harness = create_harness(tmp_path, page_count=1)
    try:
        generation_id = harness.repository.create_generation(
            "alpha",
            "chapter-1",
            semantic_fingerprint="ocr-claim",
            semantic_settings={"pipelineVersion": "progressive-segment-v2"},
            page_indexes=[0],
            source_pages={0: "https://img.example/0.png"},
            kind="normal",
            progressive=True,
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
                    "display_bottom": 180,
                    "ocr_top": 0,
                    "ocr_bottom": 180,
                    "ocr_input_path": "segments/0.png",
                }
            ],
        )

        assert harness.repository.claim_segment_ocr(generation_id, 0, 0) is True
        assert harness.repository.claim_segment_ocr(generation_id, 0, 0) is False
        claimed = harness.repository.segment(generation_id, 0, 0)
        assert claimed is not None
        assert claimed["status"] == "ocr"
        assert claimed["attempts"] == 1

        assert harness.repository.mark_segment_ocr_ready(
            generation_id,
            0,
            0,
            ocr_path="ocr/0.json",
            blocks_path="blocks/0.json",
        ) is True
        ready = harness.repository.segment(generation_id, 0, 0)
        assert ready is not None
        assert ready["status"] == "pending"
        assert ready["ocr_path"] == "ocr/0.json"
        assert ready["blocks_path"] == "blocks/0.json"

        assert harness.repository.claim_segment_ocr(generation_id, 0, 0) is True
        assert harness.repository.reset_segment_ocr(generation_id, 0, 0) is True
        reset = harness.repository.segment(generation_id, 0, 0)
        assert reset is not None
        assert reset["status"] == "pending"
        assert reset["attempts"] == 2
        assert reset["ocr_path"] == "ocr/0.json"
        assert reset["blocks_path"] == "blocks/0.json"
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


def test_background_tasks_keep_only_latest_unresolved_terminal_generation(
    tmp_path: Path,
) -> None:
    harness = create_harness(tmp_path, page_count=1)
    try:
        unresolved_id = harness.repository.create_generation(
            "alpha",
            "chapter-1",
            semantic_fingerprint="unresolved",
            semantic_settings={"pipelineVersion": "full-page-v1"},
            page_indexes=[0],
            source_pages={0: "https://img.example/0.png"},
            kind="normal",
            progressive=False,
        )
        harness.repository.fail_page(
            unresolved_id,
            0,
            stage="ocr",
            code="OCR_TIMEOUT",
            summary="simulated failure",
        )
        harness.repository.set_generation_status(unresolved_id, "completed_with_errors")

        unresolved = harness.repository.background_tasks()

        assert len(unresolved) == 1
        assert unresolved[0].generation_id == unresolved_id
        assert unresolved[0].status == "completed_with_errors"
        assert unresolved[0].stage == "needs_retry"
        assert unresolved[0].failed_pages == 1

        active_id = harness.repository.create_generation(
            "alpha",
            "chapter-1",
            semantic_fingerprint="new-active",
            semantic_settings={"pipelineVersion": "progressive-segment-v1"},
            page_indexes=[0],
            source_pages={0: "https://img.example/0.png"},
            kind="retranslate",
            progressive=True,
        )
        active = harness.repository.background_tasks()

        assert len(active) == 1
        assert active[0].generation_id == active_id
        assert active[0].status == "preparing"

        harness.repository.set_generation_status(active_id, "completed")

        assert harness.repository.background_tasks() == []

        failed_id = harness.repository.create_generation(
            "beta",
            "chapter-2",
            semantic_fingerprint="chapter-failed",
            semantic_settings={"pipelineVersion": "progressive-segment-v1"},
            page_indexes=[0],
            source_pages={0: "https://img.example/1.png"},
            kind="normal",
            progressive=True,
        )
        harness.repository.set_generation_status(failed_id, "failed")

        failed = harness.repository.background_tasks()

        assert len(failed) == 1
        assert failed[0].generation_id == failed_id
        assert failed[0].status == "failed"
        assert failed[0].stage == "needs_retry"
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
        assert 1 <= len(harness.source.media_calls) <= 3
        assert 1 <= during_ocr.total_segments <= 3
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
async def test_first_page_starts_ocr_while_second_page_is_still_loading(
    tmp_path: Path,
) -> None:
    harness = create_harness(tmp_path, page_count=2)
    harness.source.block_page_index = 1
    harness.pipeline.block_first_ocr = True
    try:
        started = await harness.manager.start("alpha", "chapter-1")
        await wait_for(harness.source.blocked_page_started.is_set)
        await wait_for(harness.pipeline.ocr_started.is_set)

        streaming = harness.manager.state("alpha", "chapter-1")
        assert streaming.generation_id == started.generation_id
        assert streaming.status == "running"
        assert streaming.planning_complete is False
        assert streaming.total_segments == 1
        assert streaming.completed_segments == 0
        assert streaming.current_segment is not None
        assert streaming.current_segment.page_index == 0
        assert harness.repository.page(str(started.generation_id), 0)["prepared"] == 1
        assert harness.repository.page(str(started.generation_id), 1)["prepared"] == 0

        harness.pipeline.ocr_release.set()
        harness.source.blocked_page_release.set()
        await wait_for(lambda: harness.manager.state("alpha", "chapter-1").status == "completed")

        completed = harness.manager.state("alpha", "chapter-1")
        assert completed.planning_complete is True
        assert (completed.completed_segments, completed.total_segments) == (2, 2)
    finally:
        harness.pipeline.ocr_release.set()
        harness.source.blocked_page_release.set()
        await harness.close()


@pytest.mark.asyncio
async def test_consumer_waits_for_more_segments_until_planning_is_complete(
    tmp_path: Path,
) -> None:
    harness = create_harness(tmp_path, page_count=2)
    harness.source.block_page_index = 1
    try:
        await harness.manager.start("alpha", "chapter-1")
        await wait_for(harness.source.blocked_page_started.is_set)
        await wait_for(
            lambda: harness.manager.state("alpha", "chapter-1").completed_segments == 1
        )

        waiting = harness.manager.state("alpha", "chapter-1")
        assert waiting.status == "running"
        assert waiting.planning_complete is False
        assert (waiting.completed_segments, waiting.total_segments) == (1, 1)
        assert waiting.current_segment is None

        harness.source.blocked_page_release.set()
        await wait_for(lambda: harness.manager.state("alpha", "chapter-1").status == "completed")
        completed = harness.manager.state("alpha", "chapter-1")
        assert completed.planning_complete is True
        assert (completed.completed_segments, completed.total_segments) == (2, 2)
    finally:
        harness.source.blocked_page_release.set()
        await harness.close()


@pytest.mark.asyncio
async def test_streaming_segment_denominator_grows_from_five_to_ten(
    tmp_path: Path,
) -> None:
    harness = create_harness(
        tmp_path,
        page_count=2,
        image_size=(120, 8000),
        translation_settings={
            "long_image_threshold": 1000,
            "ocr_slice_height": 1800,
            "ocr_slice_overlap": 200,
        },
    )
    harness.source.block_page_index = 1
    harness.pipeline.block_ocr_call = 4
    try:
        await harness.manager.start("alpha", "chapter-1")
        await wait_for(harness.source.blocked_page_started.is_set)
        await wait_for(harness.pipeline.ocr_started.is_set)

        first_page = harness.manager.state("alpha", "chapter-1")
        assert first_page.planning_complete is False
        assert (first_page.completed_segments, first_page.total_segments) == (3, 5)
        assert first_page.current_segment is not None
        assert (
            first_page.current_segment.page_index,
            first_page.current_segment.segment_index,
        ) == (0, 3)

        harness.source.blocked_page_release.set()
        await wait_for(
            lambda: harness.manager.state("alpha", "chapter-1").total_segments == 10
        )
        grown = harness.manager.state("alpha", "chapter-1")
        assert grown.planning_complete is True
        assert (grown.completed_segments, grown.total_segments) == (3, 10)
        assert grown.current_segment is not None
        assert (grown.current_segment.page_index, grown.current_segment.segment_index) == (0, 3)

        harness.pipeline.ocr_release.set()
        await wait_for(lambda: harness.manager.state("alpha", "chapter-1").status == "completed")
        completed = harness.manager.state("alpha", "chapter-1")
        assert (completed.completed_segments, completed.total_segments) == (10, 10)
        assert [
            (segment.page_index, segment.segment_index, segment.global_index)
            for page in completed.pages
            for segment in page.segments
        ] == [
            (page_index, segment_index, page_index * 5 + segment_index)
            for page_index in range(2)
            for segment_index in range(5)
        ]
    finally:
        harness.pipeline.ocr_release.set()
        harness.source.blocked_page_release.set()
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
async def test_ocr_prefetch_finishes_out_of_order_but_translates_in_order(
    tmp_path: Path,
) -> None:
    harness = create_harness(
        tmp_path,
        page_count=1,
        image_size=(300, 2400),
        translation_settings={
            "ocr_concurrency": 3,
            "long_image_threshold": 1000,
            "ocr_slice_height": 700,
            "ocr_slice_overlap": 100,
        },
    )
    harness.pipeline.blocked_ocr_segments.update({(0, 0), (0, 1), (0, 2)})
    try:
        await harness.manager.start("alpha", "chapter-1")
        await wait_for(lambda: harness.pipeline.ocr_calls == 3)

        running = harness.manager.state("alpha", "chapter-1")
        assert harness.pipeline.active_ocr_calls == 3
        assert harness.pipeline.max_active_ocr_calls == 3
        assert running.completed_segments == 0
        assert running.current_segment is not None
        assert running.current_segment.segment_index == 0

        harness.pipeline.ocr_segment_releases[(0, 2)].set()
        harness.pipeline.ocr_segment_releases[(0, 1)].set()
        await wait_for(
            lambda: {(0, 1), (0, 2)}.issubset(harness.pipeline.completed_ocr_segments)
        )
        await wait_for(lambda: harness.pipeline.ocr_calls == 4)
        assert harness.pipeline.translation_calls == 0
        assert harness.manager.state("alpha", "chapter-1").completed_segments == 0

        harness.pipeline.ocr_segment_releases[(0, 0)].set()
        await wait_for(lambda: harness.manager.state("alpha", "chapter-1").status == "completed")

        generation_id = str(harness.manager.state("alpha", "chapter-1").generation_id)
        expected_translation_inputs: list[list[str]] = []
        for segment in harness.repository.segments(generation_id):
            blocks_media = harness.cache.read_bytes(str(segment["blocks_path"]), verify_image=False)
            assert blocks_media is not None
            expected_translation_inputs.append(
                [str(value["text"]) for value in json.loads(blocks_media.content)]
            )
        assert harness.pipeline.translation_inputs == expected_translation_inputs
        assert [
            layer.segment_index
            for layer in harness.manager.state("alpha", "chapter-1").pages[0].translation_layers
        ] == [0, 1, 2, 3]
    finally:
        for event in harness.pipeline.ocr_segment_releases.values():
            event.set()
        await harness.close()


@pytest.mark.asyncio
async def test_running_generation_uses_increased_ocr_concurrency_immediately(
    tmp_path: Path,
) -> None:
    harness = create_harness(
        tmp_path,
        page_count=1,
        image_size=(300, 2400),
        translation_settings={
            "ocr_concurrency": 1,
            "long_image_threshold": 1000,
            "ocr_slice_height": 700,
            "ocr_slice_overlap": 100,
        },
    )
    harness.pipeline.blocked_ocr_calls.update({1, 2, 3})
    try:
        await harness.manager.start("alpha", "chapter-1")
        await wait_for(lambda: harness.pipeline.ocr_calls == 1)
        assert harness.pipeline.active_ocr_calls == 1

        harness.manager.set_ocr_concurrency(3)
        await wait_for(lambda: harness.pipeline.ocr_calls == 3)
        assert harness.pipeline.active_ocr_calls == 3

        for event in harness.pipeline.ocr_call_releases.values():
            event.set()
        await wait_for(lambda: harness.manager.state("alpha", "chapter-1").status == "completed")
        assert harness.pipeline.max_active_ocr_calls == 3
    finally:
        for event in harness.pipeline.ocr_call_releases.values():
            event.set()
        await harness.close()


@pytest.mark.asyncio
async def test_one_prefetched_ocr_failure_does_not_cancel_other_segments(
    tmp_path: Path,
) -> None:
    harness = create_harness(
        tmp_path,
        page_count=1,
        image_size=(300, 2400),
        translation_settings={
            "ocr_concurrency": 3,
            "long_image_threshold": 1000,
            "ocr_slice_height": 700,
            "ocr_slice_overlap": 100,
        },
    )
    harness.pipeline.fail_ocr_segments.add((0, 1))
    try:
        await harness.manager.start("alpha", "chapter-1")
        await wait_for(
            lambda: harness.manager.state("alpha", "chapter-1").status
            == "completed_with_errors"
        )

        failed = harness.manager.state("alpha", "chapter-1")
        assert (failed.completed_segments, failed.failed_segments) == (3, 1)
        assert failed.pages[0].segments[1].error is not None
        assert failed.pages[0].segments[1].error.code == "OCR_TIMEOUT"
        assert harness.pipeline.translation_calls == 3

        harness.pipeline.fail_ocr_segments.clear()
        await harness.manager.retry_segment("alpha", "chapter-1", 0, 1)
        await wait_for(lambda: harness.manager.state("alpha", "chapter-1").status == "completed")

        retried = harness.manager.state("alpha", "chapter-1")
        assert (retried.completed_segments, retried.failed_segments) == (4, 0)
        assert harness.pipeline.translation_calls == 4
    finally:
        harness.pipeline.fail_ocr_segments.clear()
        await harness.close()


@pytest.mark.asyncio
async def test_manual_ocr_retry_submits_a_fresh_cloud_job(tmp_path: Path) -> None:
    harness = create_harness(tmp_path, page_count=1)
    harness.pipeline.fail_ocr_segments.add((0, 0))
    try:
        await harness.manager.start("alpha", "chapter-1")
        await wait_for(
            lambda: harness.manager.state("alpha", "chapter-1").status == "completed_with_errors"
        )

        failed = harness.repository.segment(
            str(harness.manager.state("alpha", "chapter-1").generation_id),
            0,
            0,
        )
        assert failed is not None
        assert failed["error_code"] == "OCR_TIMEOUT"
        assert failed["ocr_job_id"] == "fake-job-1"

        harness.pipeline.fail_ocr_segments.clear()
        await harness.manager.retry_segment("alpha", "chapter-1", 0, 0)
        await wait_for(lambda: harness.manager.state("alpha", "chapter-1").status == "completed")

        completed = harness.manager.state("alpha", "chapter-1")
        segment = harness.repository.segment(str(completed.generation_id), 0, 0)
        assert segment is not None
        assert harness.pipeline.ocr_job_inputs == [None, None]
        assert harness.pipeline.submitted_ocr_jobs == ["fake-job-1", "fake-job-2"]
        assert segment["ocr_job_id"] == "fake-job-2"
        assert completed.pages[0].segments[0].attempts == 2
        assert completed.pages[0].segments[0].error is None
    finally:
        harness.pipeline.fail_ocr_segments.clear()
        await harness.close()


def test_prepare_failed_retries_is_stage_aware_and_idempotent(tmp_path: Path) -> None:
    harness = create_harness(tmp_path, page_count=1)
    try:
        generation_id = harness.repository.create_generation(
            "alpha",
            "chapter-1",
            semantic_fingerprint="bulk-retry-checkpoints",
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
            original_path="original/0.png",
            original_checksum="checksum",
            width=120,
            height=400,
        )
        harness.repository.commit_segment_plan(
            generation_id,
            [
                {
                    "page_index": 0,
                    "segment_index": index,
                    "global_index": index,
                    "source_width": 120,
                    "source_height": 400,
                    "display_top": index * 100,
                    "display_bottom": (index + 1) * 100,
                    "ocr_top": index * 100,
                    "ocr_bottom": (index + 1) * 100,
                    "ocr_input_path": f"input/{index}.png",
                }
                for index in range(4)
            ],
        )
        for index, stage, status in [
            (0, "ocr", "failed"),
            (1, "translation", "failed"),
            (2, "rendering", "failed"),
            (3, None, "completed"),
        ]:
            harness.database.execute(
                """
                UPDATE translation_segments SET status = ?, ocr_path = ?, blocks_path = ?,
                    translations_path = ?, translated_path = ?, translated_version = ?,
                    ocr_job_id = ?, error_stage = ?, error_code = ?, error_summary = ?,
                    attempts = 1
                WHERE generation_id = ? AND page_index = 0 AND segment_index = ?
                """,
                (
                    status,
                    f"ocr/{index}.json",
                    f"blocks/{index}.json",
                    f"translations/{index}.json",
                    f"translated/{index}.png",
                    f"version-{index}",
                    f"job-{index}",
                    stage,
                    "SIMULATED_FAILURE" if stage else None,
                    "simulated failure" if stage else None,
                    generation_id,
                    index,
                ),
            )
        harness.database.execute(
            """
            UPDATE translation_pages SET status = 'failed', error_stage = 'segment',
                error_code = 'SEGMENTS_FAILED', error_summary = '3 failed'
            WHERE generation_id = ? AND page_index = 0
            """,
            (generation_id,),
        )
        harness.database.execute(
            """
            UPDATE translation_generations SET status = 'completed_with_errors',
                completed_segments = 1, failed_segments = 3
            WHERE generation_id = ?
            """,
            (generation_id,),
        )

        retried_count, cache_paths = harness.repository.prepare_failed_retries(generation_id)

        assert retried_count == 3
        assert set(cache_paths) == {
            "ocr/0.json",
            "blocks/0.json",
            "translations/0.json",
            "translated/0.png",
            "translations/1.json",
            "translated/1.png",
            "translated/2.png",
        }
        segments = harness.repository.segments(generation_id)
        assert [str(segment["status"]) for segment in segments] == [
            "pending",
            "pending",
            "pending",
            "completed",
        ]
        assert segments[0]["ocr_path"] is None
        assert segments[0]["blocks_path"] is None
        assert segments[0]["translations_path"] is None
        assert segments[0]["translated_path"] is None
        assert segments[0]["ocr_job_id"] is None
        assert segments[0]["attempts"] == 1
        assert segments[1]["ocr_path"] == "ocr/1.json"
        assert segments[1]["blocks_path"] == "blocks/1.json"
        assert segments[1]["translations_path"] is None
        assert segments[1]["translated_path"] is None
        assert segments[1]["ocr_job_id"] == "job-1"
        assert segments[1]["attempts"] == 2
        assert segments[2]["translations_path"] == "translations/2.json"
        assert segments[2]["translated_path"] is None
        assert segments[2]["attempts"] == 2
        assert segments[3]["translated_path"] == "translated/3.png"
        assert all(segment["error_stage"] is None for segment in segments[:3])
        page = harness.repository.page(generation_id, 0)
        assert page is not None
        assert page["status"] == "pending"
        assert page["error_code"] is None
        generation = harness.repository.generation(generation_id)
        assert generation is not None
        assert (generation["completed_segments"], generation["failed_segments"]) == (1, 0)

        repeated_count, repeated_paths = harness.repository.prepare_failed_retries(generation_id)

        assert repeated_count == 0
        assert repeated_paths == []
        assert [segment["attempts"] for segment in harness.repository.segments(generation_id)] == [
            1,
            2,
            2,
            1,
        ]
    finally:
        harness.database.close()


def test_prepare_failed_retries_supports_legacy_pages(tmp_path: Path) -> None:
    harness = create_harness(tmp_path, page_count=2)
    try:
        generation_id = harness.repository.create_generation(
            "alpha",
            "chapter-1",
            semantic_fingerprint="legacy-bulk-retry",
            semantic_settings={"pipelineVersion": "full-page-v1"},
            page_indexes=[0, 1],
            source_pages={
                0: "https://img.example/0.png",
                1: "https://img.example/1.png",
            },
            kind="normal",
            progressive=False,
        )
        harness.database.execute(
            """
            UPDATE translation_pages SET status = 'failed', original_path = 'original/0.png',
                ocr_path = 'ocr/0.json', blocks_path = 'blocks/0.json',
                translations_path = 'translations/0.json',
                translated_path = 'translated/0.png', translated_version = 'old-version',
                error_stage = 'translation', error_code = 'TRANSLATION_TIMEOUT',
                error_summary = 'simulated failure', attempts = 2
            WHERE generation_id = ? AND page_index = 0
            """,
            (generation_id,),
        )
        harness.database.execute(
            """
            UPDATE translation_pages SET status = 'completed', translated_path = 'translated/1.png',
                translated_version = 'completed-version'
            WHERE generation_id = ? AND page_index = 1
            """,
            (generation_id,),
        )
        harness.database.execute(
            """
            UPDATE translation_generations SET status = 'completed_with_errors',
                completed_pages = 1, failed_pages = 1
            WHERE generation_id = ?
            """,
            (generation_id,),
        )

        retried_count, cache_paths = harness.repository.prepare_failed_retries(generation_id)

        assert retried_count == 1
        assert cache_paths == ["translations/0.json", "translated/0.png"]
        failed_page = harness.repository.page(generation_id, 0)
        completed_page = harness.repository.page(generation_id, 1)
        assert failed_page is not None and completed_page is not None
        assert failed_page["status"] == "pending"
        assert failed_page["ocr_path"] == "ocr/0.json"
        assert failed_page["blocks_path"] == "blocks/0.json"
        assert failed_page["translations_path"] is None
        assert failed_page["translated_path"] is None
        assert failed_page["attempts"] == 2
        assert completed_page["status"] == "completed"
        generation = harness.repository.generation(generation_id)
        assert generation is not None
        assert (generation["completed_pages"], generation["failed_pages"]) == (1, 0)
    finally:
        harness.database.close()


@pytest.mark.asyncio
async def test_retry_failed_resumes_same_generation_and_is_idempotent(tmp_path: Path) -> None:
    harness = create_harness(tmp_path, page_count=2)
    harness.pipeline.fail_ocr_segments.update({(0, 0), (1, 0)})
    try:
        started = await harness.manager.start("alpha", "chapter-1")
        await wait_for(
            lambda: harness.manager.state("alpha", "chapter-1").status
            == "completed_with_errors"
        )
        waiting_retry = harness.manager.background_tasks()
        assert len(waiting_retry) == 1
        assert waiting_retry[0].stage == "needs_retry"

        harness.pipeline.fail_ocr_segments.clear()
        retried, retried_count = await harness.manager.retry_failed("alpha", "chapter-1")
        repeated, repeated_count = await harness.manager.retry_failed("alpha", "chapter-1")

        assert retried.generation_id == started.generation_id
        assert retried_count == 2
        assert repeated.generation_id == started.generation_id
        assert repeated_count == 0
        await wait_for(lambda: harness.manager.state("alpha", "chapter-1").status == "completed")
        completed = harness.manager.state("alpha", "chapter-1")
        assert (completed.completed_segments, completed.failed_segments) == (2, 0)
        assert harness.manager.background_tasks() == []
    finally:
        harness.pipeline.fail_ocr_segments.clear()
        await harness.close()


@pytest.mark.asyncio
async def test_retry_failed_finishes_current_segment_then_prioritizes_earlier_failure(
    tmp_path: Path,
) -> None:
    harness = create_harness(
        tmp_path,
        page_count=1,
        image_size=(300, 2400),
        translation_settings={
            "ocr_concurrency": 1,
            "long_image_threshold": 1000,
            "ocr_slice_height": 700,
            "ocr_slice_overlap": 100,
        },
    )
    harness.pipeline.fail_ocr_segments.add((0, 0))
    harness.pipeline.block_next_render = True
    try:
        await harness.manager.start("alpha", "chapter-1")
        await wait_for(harness.pipeline.render_started.is_set)
        rendering = harness.manager.state("alpha", "chapter-1")
        assert rendering.current_segment is not None
        assert rendering.current_segment.segment_index == 1
        assert rendering.pages[0].segments[0].status == "failed"

        harness.pipeline.fail_ocr_segments.clear()
        harness.pipeline.blocked_ocr_segments.add((0, 0))
        _retried, retried_count = await harness.manager.retry_failed("alpha", "chapter-1")

        still_rendering = harness.manager.state("alpha", "chapter-1")
        assert retried_count == 1
        assert still_rendering.current_segment is not None
        assert still_rendering.current_segment.segment_index == 1
        assert still_rendering.pages[0].segments[1].status == "rendering"

        harness.pipeline.render_release.set()
        await wait_for(
            lambda: (release := harness.pipeline.ocr_segment_releases.get((0, 0))) is not None
            and not release.is_set()
        )
        prioritized = harness.manager.state("alpha", "chapter-1")
        assert prioritized.current_segment is not None
        assert prioritized.current_segment.segment_index == 0
        assert prioritized.pages[0].segments[1].status == "completed"

        harness.pipeline.ocr_segment_releases[(0, 0)].set()
        await wait_for(lambda: harness.manager.state("alpha", "chapter-1").status == "completed")
    finally:
        harness.pipeline.fail_ocr_segments.clear()
        harness.pipeline.blocked_ocr_segments.clear()
        harness.pipeline.render_release.set()
        for release in harness.pipeline.ocr_segment_releases.values():
            release.set()
        await harness.close()


@pytest.mark.asyncio
async def test_retry_failed_rejects_a_generation_that_is_stopping(tmp_path: Path) -> None:
    harness = create_harness(tmp_path, page_count=1)
    harness.pipeline.fail_ocr_segments.add((0, 0))
    try:
        await harness.manager.start("alpha", "chapter-1")
        await wait_for(
            lambda: harness.manager.state("alpha", "chapter-1").status
            == "completed_with_errors"
        )
        generation_id = str(harness.manager.state("alpha", "chapter-1").generation_id)
        harness.repository.set_generation_status(
            generation_id,
            "stopping_after_segment",
            stop_requested=True,
        )

        with pytest.raises(AppError) as captured:
            await harness.manager.retry_failed("alpha", "chapter-1")

        assert captured.value.code == "TRANSLATION_STOPPING"
        assert captured.value.status_code == 409
        assert harness.manager.state("alpha", "chapter-1").failed_segments == 1
    finally:
        harness.pipeline.fail_ocr_segments.clear()
        await harness.close()


@pytest.mark.asyncio
async def test_lower_ocr_concurrency_waits_for_existing_permits_to_finish(
    tmp_path: Path,
) -> None:
    harness = create_harness(
        tmp_path,
        page_count=1,
        image_size=(300, 2400),
        translation_settings={
            "ocr_concurrency": 3,
            "long_image_threshold": 1000,
            "ocr_slice_height": 700,
            "ocr_slice_overlap": 100,
        },
    )
    harness.pipeline.blocked_ocr_calls.update({1, 2, 3, 4})
    try:
        await harness.manager.start("alpha", "chapter-1")
        await wait_for(lambda: harness.pipeline.ocr_calls == 3)

        harness.manager.set_ocr_concurrency(1)
        harness.pipeline.ocr_call_releases[1].set()
        harness.pipeline.ocr_call_releases[2].set()
        await wait_for(lambda: {1, 2}.issubset(harness.pipeline.completed_ocr_calls))
        await asyncio.sleep(0.05)
        assert harness.pipeline.ocr_calls == 3

        harness.pipeline.ocr_call_releases[3].set()
        await wait_for(lambda: harness.pipeline.ocr_calls == 4)
        assert harness.pipeline.active_ocr_calls == 1
        harness.pipeline.ocr_call_releases[4].set()

        await wait_for(lambda: harness.manager.state("alpha", "chapter-1").status == "completed")
        assert harness.pipeline.max_active_ocr_calls == 3
    finally:
        for event in harness.pipeline.ocr_call_releases.values():
            event.set()
        await harness.close()


@pytest.mark.asyncio
async def test_multiple_chapters_share_one_ocr_concurrency_limit(tmp_path: Path) -> None:
    harness = create_harness(
        tmp_path,
        page_count=1,
        image_size=(300, 2400),
        translation_settings={
            "ocr_concurrency": 2,
            "long_image_threshold": 1000,
            "ocr_slice_height": 700,
            "ocr_slice_overlap": 100,
        },
    )
    harness.pipeline.blocked_ocr_calls.update({1, 2, 3, 4})
    try:
        await harness.manager.start("alpha", "chapter-1")
        await harness.manager.start("beta", "chapter-2")
        await wait_for(lambda: harness.pipeline.ocr_calls == 2)
        await asyncio.sleep(0.05)
        assert harness.pipeline.ocr_calls == 2
        assert harness.pipeline.max_active_ocr_calls == 2

        harness.pipeline.ocr_call_releases[1].set()
        await wait_for(lambda: harness.pipeline.ocr_calls == 3)
        assert harness.pipeline.active_ocr_calls == 2

        harness.pipeline.blocked_ocr_calls.clear()
        for event in harness.pipeline.ocr_call_releases.values():
            event.set()
        await wait_for(lambda: harness.manager.state("alpha", "chapter-1").status == "completed")
        await wait_for(lambda: harness.manager.state("beta", "chapter-2").status == "completed")
        assert harness.pipeline.max_active_ocr_calls == 2
    finally:
        harness.pipeline.blocked_ocr_calls.clear()
        for event in harness.pipeline.ocr_call_releases.values():
            event.set()
        await harness.close()


@pytest.mark.asyncio
async def test_pause_keeps_current_ocr_and_cancels_later_prefetch(tmp_path: Path) -> None:
    harness = create_harness(
        tmp_path,
        page_count=1,
        image_size=(300, 2400),
        translation_settings={
            "ocr_concurrency": 3,
            "long_image_threshold": 1000,
            "ocr_slice_height": 700,
            "ocr_slice_overlap": 100,
        },
    )
    harness.pipeline.blocked_ocr_segments.update({(0, 0), (0, 1), (0, 2)})
    try:
        await harness.manager.start("alpha", "chapter-1")
        await wait_for(lambda: harness.pipeline.ocr_calls == 3)

        stopping = await harness.manager.pause("alpha", "chapter-1")
        assert stopping.status == "stopping_after_segment"
        await wait_for(lambda: harness.pipeline.active_ocr_calls == 1)

        during_stop = harness.manager.state("alpha", "chapter-1")
        assert during_stop.current_segment is not None
        assert during_stop.current_segment.segment_index == 0
        assert [segment.status for segment in during_stop.pages[0].segments] == [
            "ocr",
            "pending",
            "pending",
            "pending",
        ]

        harness.pipeline.ocr_segment_releases[(0, 0)].set()
        await wait_for(lambda: harness.manager.state("alpha", "chapter-1").status == "paused")
        paused = harness.manager.state("alpha", "chapter-1")
        assert paused.completed_segments == 1
        assert len(harness.pipeline.completed_ocr_calls) == 1

        harness.pipeline.blocked_ocr_segments.clear()
        await harness.manager.start("alpha", "chapter-1")
        await wait_for(lambda: harness.manager.state("alpha", "chapter-1").status == "completed")
        assert harness.manager.state("alpha", "chapter-1").completed_segments == 4
    finally:
        harness.pipeline.blocked_ocr_segments.clear()
        for event in harness.pipeline.ocr_segment_releases.values():
            event.set()
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
        assert deeplx_pipeline.ocr.limiter is harness.manager._ocr_limiter
        assert deeplx_pipeline.ocr.protocol == "direct"
        assert deeplx_pipeline.ocr.auth_mode == "none"

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
        assert deepl_semantic["ocrProtocol"] == "direct"
        assert "ocrMode" not in deepl_semantic
        assert deepl_fingerprint != deeplx_fingerprint
    finally:
        await harness.close()


@pytest.mark.asyncio
async def test_manager_fingerprints_resolved_ocr_protocol_and_defaults_legacy_to_job(
    tmp_path: Path,
) -> None:
    harness = create_harness(tmp_path, page_count=1)
    try:
        runtime = harness.manager._runtime_settings(require_services=True)
        auto_semantic, auto_fingerprint = harness.manager._semantic_settings(
            {0: "https://img.example/0.png"},
            runtime,
        )

        runtime["ocr_mode"] = "direct"
        direct_semantic, direct_fingerprint = harness.manager._semantic_settings(
            {0: "https://img.example/0.png"},
            runtime,
        )

        runtime["ocr_mode"] = "job"
        job_semantic, job_fingerprint = harness.manager._semantic_settings(
            {0: "https://img.example/0.png"},
            runtime,
        )

        assert auto_semantic["ocrProtocol"] == "direct"
        assert direct_semantic["ocrProtocol"] == "direct"
        assert auto_fingerprint == direct_fingerprint
        assert job_semantic["ocrProtocol"] == "job"
        assert job_fingerprint != direct_fingerprint
        assert (
            not {
                "ocrApiUrl",
                "ocrAuthMode",
                "ocrToken",
                "ocrBasicUsername",
                "ocrBasicPassword",
            }
            & auto_semantic.keys()
        )

        direct_pipeline = harness.manager._build_pipeline(direct_semantic, runtime)
        job_pipeline = harness.manager._build_pipeline(job_semantic, runtime)
        legacy_semantic = dict(direct_semantic)
        legacy_semantic.pop("ocrProtocol")
        legacy_pipeline = harness.manager._build_pipeline(legacy_semantic, runtime)

        assert direct_pipeline.ocr.protocol == "direct"
        assert job_pipeline.ocr.protocol == "job"
        assert legacy_pipeline.ocr.protocol == "job"
    finally:
        await harness.close()


@pytest.mark.asyncio
@pytest.mark.parametrize(
    ("ocr_settings", "expected_error"),
    [
        ({"ocr_auth_mode": "none", "ocr_token": ""}, None),
        ({"ocr_auth_mode": "bearer", "ocr_token": "configured-token"}, None),
        ({"ocr_auth_mode": "bearer", "ocr_token": ""}, "OCR_AUTH_NOT_CONFIGURED"),
        (
            {
                "ocr_auth_mode": "basic",
                "ocr_token": "",
                "ocr_basic_username": "test-user",
                "ocr_basic_password": "test-password",
            },
            None,
        ),
        (
            {
                "ocr_auth_mode": "basic",
                "ocr_basic_username": "test-user",
                "ocr_basic_password": "",
            },
            "OCR_AUTH_NOT_CONFIGURED",
        ),
    ],
)
async def test_manager_validates_only_credentials_required_by_ocr_auth_mode(
    tmp_path: Path,
    ocr_settings: dict[str, object],
    expected_error: str | None,
) -> None:
    harness = create_harness(tmp_path, page_count=1, translation_settings=ocr_settings)
    try:
        if expected_error is None:
            harness.manager._runtime_settings(require_services=True)
            return
        with pytest.raises(AppError) as error:
            harness.manager._runtime_settings(require_services=True)
        assert error.value.code == expected_error
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
