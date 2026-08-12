from __future__ import annotations

import asyncio
import io
from dataclasses import dataclass
from pathlib import Path

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


def make_png(color: tuple[int, int, int]) -> bytes:
    buffer = io.BytesIO()
    Image.new("RGB", (120, 180), color).save(buffer, format="PNG")
    return buffer.getvalue()


class FakeTranslationSource:
    def __init__(self, page_count: int = 3) -> None:
        self.page_count = page_count
        self.media_calls: list[str] = []

    async def chapter(self, comic_id: str, chapter_id: str) -> SourceChapterManifest:
        return SourceChapterManifest(
            comic_id=comic_id,
            chapter_id=chapter_id,
            title="Chapter 1",
            pages=[
                SourcePage(
                    index=index,
                    source_url=f"https://img01.manga18fx.com/{index}.png",
                )
                for index in range(self.page_count)
            ],
        )

    async def fetch_media(self, source_url: str) -> tuple[bytes, str]:
        self.media_calls.append(source_url)
        index = int(source_url.rsplit("/", 1)[1].split(".", 1)[0])
        return make_png((30 + index, 60, 90)), "image/png"

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


def create_harness(tmp_path: Path, *, page_count: int = 3) -> ManagerHarness:
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
        initial_settings={
            "ocr_api_url": "https://ocr.example/api",
            "deeplx_url": "https://translate.example/api",
            "ocr_auth_mode": "none",
        },
    )
    config.ensure_directories()
    database = Database(config.database_path)
    cipher = SecretCipher(config.secrets_path, database)
    settings = SettingsService(database, cipher, config)
    source = FakeTranslationSource(page_count)
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


@pytest.mark.asyncio
async def test_pause_finishes_current_source_image_then_resumes_next(
    tmp_path: Path,
) -> None:
    harness = create_harness(tmp_path)
    harness.pipeline.block_first_ocr = True
    try:
        started = await harness.manager.start("alpha", "chapter-1")
        await wait_for(harness.pipeline.ocr_started.is_set)

        stopping = await harness.manager.pause("alpha", "chapter-1")
        assert started.generation_id == stopping.generation_id
        assert stopping.status == "stopping_after_page"
        assert stopping.current_page_index == 0

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
        assert failed.pages[0].error.code == "TRANSLATION_TIMEOUT"
        assert harness.pipeline.ocr_calls == 2
        assert harness.pipeline.translation_calls == 2

        await harness.manager.retry_page("alpha", "chapter-1", 0)
        await wait_for(lambda: harness.manager.state("alpha", "chapter-1").status == "completed")
        retried = harness.manager.state("alpha", "chapter-1")
        assert [page.status for page in retried.pages] == [
            "completed",
            "completed",
        ]
        assert harness.pipeline.ocr_calls == 2
        assert harness.pipeline.translation_calls == 3
        assert retried.pages[0].attempts == 2
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
        assert first_active is not None
        first_version = str(first_active["translated_version"])

        harness.pipeline.block_next_render = True
        second = await harness.manager.retranslate("alpha", "chapter-1")
        duplicate = await harness.manager.retranslate("alpha", "chapter-1")
        assert second.generation_id != first.generation_id
        assert duplicate.generation_id == second.generation_id
        await wait_for(harness.pipeline.render_started.is_set)

        during = harness.repository.active_page("alpha", "chapter-1", 0)
        assert during is not None
        assert str(during["translated_version"]) == first_version

        harness.pipeline.render_release.set()
        await wait_for(lambda: harness.manager.state("alpha", "chapter-1").status == "completed")
        after = harness.repository.active_page("alpha", "chapter-1", 0)
        assert after is not None
        assert str(after["generation_id"]) == second.generation_id
        assert str(after["translated_version"]) != first_version
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
