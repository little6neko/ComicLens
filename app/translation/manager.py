from __future__ import annotations

import asyncio
import hashlib
import io
import json
import logging
from collections.abc import Callable
from typing import Any

import httpx
from PIL import Image

from app.application.settings import SettingsService
from app.cache.keys import (
    chapter_bundle_key,
    generation_page_path,
    original_path,
)
from app.cache.storage import MediaCache
from app.domain.comic import ChapterManifest
from app.domain.translation import (
    BackgroundTranslationTask,
    ForceStopTranslationResult,
    TranslationTaskState,
)
from app.errors import AppError
from app.media.registry import SourceMediaRegistry
from app.observability import log_event, logged_stage, task_log_context
from app.repositories.translation import (
    TranslationRepository,
    page_retry_clear_columns,
    segment_retry_clear_columns,
)
from app.sources.base import ComicSource
from app.translation.concurrency import DynamicConcurrencyLimiter
from app.translation.image_renderer import (
    RENDERER_VERSION,
    font_identity,
    sanitize_image,
)
from app.translation.models import TextBlock
from app.translation.ocr import (
    OCRClient,
    OCRJobFailedError,
    OCRJobNotFoundError,
    OCRProtocolError,
    resolve_ocr_protocol,
)
from app.translation.pipeline import ImageTranslationPipeline, PipelineSettings
from app.translation.segment_planner import SegmentPlanner
from app.translation.segment_runner import SegmentRunner
from app.translation.translator import (
    DeepLAuthenticationError,
    DeepLClient,
    DeepLQuotaExceededError,
    DeepLRateLimitError,
    DeepLXClient,
    TranslationInputTooLargeError,
    TranslationProtocolError,
)

logger = logging.getLogger(__name__)

PipelineFactory = Callable[[dict[str, Any], dict[str, Any]], ImageTranslationPipeline]
PROGRESSIVE_PIPELINE_VERSION = "progressive-segment-v2"
PROGRESSIVE_PIPELINE_VERSIONS = {
    "progressive-segment-v1",
    PROGRESSIVE_PIPELINE_VERSION,
}


class TranslationManager:
    def __init__(
        self,
        *,
        repository: TranslationRepository,
        cache: MediaCache,
        source: ComicSource,
        registry: SourceMediaRegistry,
        settings: SettingsService,
        pipeline_factory: PipelineFactory | None = None,
    ) -> None:
        self.repository = repository
        self.cache = cache
        self.source = source
        self.registry = registry
        self.settings = settings
        self._tasks: dict[tuple[str, str], asyncio.Task[None]] = {}
        self._locks: dict[tuple[str, str], asyncio.Lock] = {}
        self._force_stop_locks: dict[tuple[str, str], asyncio.Lock] = {}
        self._force_stopping: set[tuple[str, str]] = set()
        self._generation_events: dict[str, asyncio.Event] = {}
        self._activity_listeners: set[Callable[[], None]] = set()
        self._shutting_down = False
        self._ocr_limiter = DynamicConcurrencyLimiter(
            int(settings.values(include_secrets=False)["ocr_concurrency"])
        )
        self._http_client = httpx.AsyncClient(timeout=httpx.Timeout(60.0))
        self._pipeline_factory = pipeline_factory or self._build_pipeline
        self._planner = SegmentPlanner(repository=repository, cache=cache, source=source)
        self._segment_runner = SegmentRunner(
            repository=repository,
            cache=cache,
            source=source,
        )
        self.repository.recover_interrupted()
        self.repository.recover_invalid_checkpoints()
        self._resume_recovered_workers()

    async def shutdown(self) -> None:
        self._shutting_down = True
        active_tasks = [task for task in self._tasks.values() if not task.done()]
        for task in active_tasks:
            task.cancel()
        if active_tasks:
            await asyncio.gather(*active_tasks, return_exceptions=True)
        self.repository.recover_interrupted()
        self._activity_listeners.clear()
        await self._http_client.aclose()

    def add_activity_listener(self, listener: Callable[[], None]) -> Callable[[], None]:
        self._activity_listeners.add(listener)

        def remove() -> None:
            self._activity_listeners.discard(listener)

        return remove

    def _notify_activity_changed(self) -> None:
        for listener in tuple(self._activity_listeners):
            try:
                listener()
            except Exception:
                logger.warning("Translation activity listener failed", exc_info=True)

    def validate_runtime_services(self) -> None:
        self._runtime_settings(require_services=True)

    def has_interactive_tasks(self) -> bool:
        return bool(self.repository.interactive_generations())

    def state_for_generation(self, generation_id: str) -> TranslationTaskState | None:
        generation = self.repository.generation(generation_id)
        if generation is None:
            return None
        return self.repository.task_state(
            str(generation["comic_id"]),
            str(generation["chapter_id"]),
            generation_id,
        )

    @property
    def ocr_concurrency(self) -> int:
        return self._ocr_limiter.limit

    def set_ocr_concurrency(self, concurrency: int) -> None:
        self._ocr_limiter.resize(concurrency)
        for event in self._generation_events.values():
            event.set()

    def _wake_generation(self, generation_id: str) -> None:
        event = self._generation_events.get(generation_id)
        if event is not None:
            event.set()

    @staticmethod
    def _log_task_event(
        event: str,
        *,
        comic_id: str,
        chapter_id: str,
        generation_id: str | None = None,
        level: int = logging.INFO,
        **fields: object,
    ) -> None:
        with task_log_context(
            generation_id=generation_id,
            comic=comic_id,
            chapter=chapter_id,
        ):
            log_event("task", event, level=level, **fields)

    def _log_terminal_generation(self, generation_id: str) -> None:
        generation = self.repository.generation(generation_id)
        if generation is None:
            return
        status = str(generation["status"])
        fields = {
            "status": status,
            "completed_pages": int(generation["completed_pages"]),
            "failed_pages": int(generation["failed_pages"]),
            "completed_segments": int(generation["completed_segments"]),
            "failed_segments": int(generation["failed_segments"]),
        }
        if status in {"completed", "completed_with_errors"}:
            log_event("task", "task_completed", **fields)
        elif status == "paused":
            log_event("task", "task_paused", **fields)

    async def start(
        self,
        comic_id: str,
        chapter_id: str,
        *,
        batch_item_id: str | None = None,
    ) -> TranslationTaskState:
        action_lock = self._force_stop_locks.setdefault((comic_id, chapter_id), asyncio.Lock())
        async with action_lock:
            return await self._start(
                comic_id,
                chapter_id,
                batch_item_id=batch_item_id,
            )

    async def _start(
        self,
        comic_id: str,
        chapter_id: str,
        *,
        batch_item_id: str | None = None,
    ) -> TranslationTaskState:
        source_pages = await self._ensure_source_pages(comic_id, chapter_id)
        runtime = self._runtime_settings(require_services=True)
        semantic, fingerprint = self._semantic_settings(source_pages, runtime)
        active = self.repository.active_generation(comic_id, chapter_id)
        latest = self.repository.latest_generation(comic_id, chapter_id)
        if (
            active is None
            and latest is not None
            and str(latest["status"]) == "paused"
            and str(latest["semantic_fingerprint"]) == fingerprint
        ):
            generation_id = str(latest["generation_id"])
            if batch_item_id is not None:
                self.repository.assign_batch_item(
                    generation_id,
                    batch_item_id,
                    comic_id,
                    chapter_id,
                )
            self.repository.resume(generation_id)
            self._log_task_event(
                "task_resumed",
                comic_id=comic_id,
                chapter_id=chapter_id,
                generation_id=generation_id,
                reason="start",
            )
            self._ensure_scoped_worker(comic_id, chapter_id, batch_item_id)
            return self.repository.task_state(comic_id, chapter_id, generation_id)
        matching = self.repository.matching_generation(comic_id, chapter_id, fingerprint)

        if active is not None and str(active["semantic_fingerprint"]) == fingerprint:
            if batch_item_id is not None:
                self.repository.assign_batch_item(
                    str(active["generation_id"]),
                    batch_item_id,
                    comic_id,
                    chapter_id,
                )
            if bool(active["stop_requested"]):
                current_page_index = (
                    int(active["current_page_index"])
                    if active["current_page_index"] is not None
                    else None
                )
                current_segment_index = (
                    int(active["current_segment_index"])
                    if active["current_segment_index"] is not None
                    else None
                )
                resumed_status = (
                    "running"
                    if current_segment_index is not None
                    else ("queued" if bool(active["planning_complete"]) else "preparing")
                )
                self.repository.set_generation_status(
                    str(active["generation_id"]),
                    resumed_status,
                    stop_requested=False,
                    current_page_index=current_page_index,
                    current_segment_index=current_segment_index,
                )
                self._log_task_event(
                    "task_resumed",
                    comic_id=comic_id,
                    chapter_id=chapter_id,
                    generation_id=str(active["generation_id"]),
                    reason="stop_cancelled",
                )
            self._ensure_scoped_worker(comic_id, chapter_id, batch_item_id)
            return self.repository.task_state(comic_id, chapter_id, str(active["generation_id"]))

        if matching is not None:
            generation_id = str(matching["generation_id"])
            if batch_item_id is not None:
                self.repository.assign_batch_item(
                    generation_id,
                    batch_item_id,
                    comic_id,
                    chapter_id,
                )
            status = str(matching["status"])
            if status == "paused":
                self.repository.resume(generation_id)
                self._log_task_event(
                    "task_resumed",
                    comic_id=comic_id,
                    chapter_id=chapter_id,
                    generation_id=generation_id,
                    reason="start",
                )
            elif status in {"completed", "completed_with_errors"}:
                return self.repository.task_state(comic_id, chapter_id, generation_id)
            elif status == "failed":
                self.repository.resume(generation_id)
                self._log_task_event(
                    "task_resumed",
                    comic_id=comic_id,
                    chapter_id=chapter_id,
                    generation_id=generation_id,
                    reason="retry",
                )
            if active is not None and str(active["generation_id"]) != generation_id:
                self.repository.request_stop(str(active["generation_id"]))
            self._ensure_scoped_worker(comic_id, chapter_id, batch_item_id)
            return self.repository.task_state(comic_id, chapter_id, generation_id)

        generation_id = self.repository.create_generation(
            comic_id,
            chapter_id,
            semantic_fingerprint=fingerprint,
            semantic_settings=semantic,
            page_indexes=sorted(source_pages),
            kind="normal",
            source_pages=source_pages,
            progressive=True,
            batch_item_id=batch_item_id,
        )
        if active is not None:
            self.repository.request_stop(str(active["generation_id"]))
        self._log_task_event(
            "task_queued",
            comic_id=comic_id,
            chapter_id=chapter_id,
            generation_id=generation_id,
            kind="normal",
        )
        self._ensure_scoped_worker(comic_id, chapter_id, batch_item_id)
        return self.repository.task_state(comic_id, chapter_id, generation_id)

    async def pause(self, comic_id: str, chapter_id: str) -> TranslationTaskState:
        active_rows = self.repository.database.fetchall(
            """
            SELECT generation_id FROM translation_generations
            WHERE comic_id = ? AND chapter_id = ?
              AND status IN (
                  'preparing', 'queued', 'running', 'stopping_after_page',
                  'stopping_after_segment'
              )
            ORDER BY created_at ASC, rowid ASC
            """,
            (comic_id, chapter_id),
        )
        if not active_rows:
            return self.repository.task_state(comic_id, chapter_id)
        for row in active_rows:
            generation_id = str(row["generation_id"])
            self.repository.request_stop(generation_id)
            self._wake_generation(generation_id)
            self._log_task_event(
                "pause_requested",
                comic_id=comic_id,
                chapter_id=chapter_id,
                generation_id=generation_id,
            )
        self._notify_activity_changed()
        latest_id = str(active_rows[-1]["generation_id"])
        return self.repository.task_state(comic_id, chapter_id, latest_id)

    async def pause_generation(self, generation_id: str) -> TranslationTaskState | None:
        generation = self.repository.generation(generation_id)
        if generation is None:
            return None
        comic_id = str(generation["comic_id"])
        chapter_id = str(generation["chapter_id"])
        if str(generation["status"]) in {
            "preparing",
            "queued",
            "running",
            "stopping_after_page",
            "stopping_after_segment",
        }:
            self.repository.request_stop(generation_id)
            self._wake_generation(generation_id)
            self._log_task_event(
                "pause_requested",
                comic_id=comic_id,
                chapter_id=chapter_id,
                generation_id=generation_id,
                reason="batch_yield",
            )
            self._notify_activity_changed()
        return self.repository.task_state(comic_id, chapter_id, generation_id)

    def background_tasks(self) -> list[BackgroundTranslationTask]:
        return self.repository.background_tasks()

    async def force_stop(
        self,
        comic_id: str,
        chapter_id: str,
    ) -> ForceStopTranslationResult:
        key = (comic_id, chapter_id)
        force_stop_lock = self._force_stop_locks.setdefault(key, asyncio.Lock())
        stopped = 0
        async with force_stop_lock:
            self._force_stopping.add(key)
            try:
                worker = self._tasks.get(key)
                if worker is not None and not worker.done():
                    worker.cancel()
                    await asyncio.gather(worker, return_exceptions=True)
                chapter_lock = self._locks.setdefault(key, asyncio.Lock())
                async with chapter_lock:
                    stopped = self.repository.force_pause_chapter(comic_id, chapter_id)
                    if stopped:
                        self.cache.set_chapter_active(comic_id, chapter_id, False)
                        self.cache.enforce_limit()
            finally:
                self._force_stopping.discard(key)
            if self.repository.active_generation(comic_id, chapter_id) is not None:
                self._ensure_worker(comic_id, chapter_id)
            if stopped:
                self._log_task_event(
                    "task_cancelled",
                    comic_id=comic_id,
                    chapter_id=chapter_id,
                    stopped_generations=stopped,
                    reason="force_stop",
                )
            self._notify_activity_changed()
            return ForceStopTranslationResult(
                comic_id=comic_id,
                chapter_id=chapter_id,
                stopped_generations=stopped,
            )

    async def retranslate(self, comic_id: str, chapter_id: str) -> TranslationTaskState:
        action_lock = self._force_stop_locks.setdefault((comic_id, chapter_id), asyncio.Lock())
        async with action_lock:
            return await self._retranslate(comic_id, chapter_id)

    async def _retranslate(self, comic_id: str, chapter_id: str) -> TranslationTaskState:
        source_pages = await self._ensure_source_pages(comic_id, chapter_id)
        runtime = self._runtime_settings(require_services=True)
        semantic, fingerprint = self._semantic_settings(source_pages, runtime)
        rows = self.repository.database.fetchall(
            """
            SELECT * FROM translation_generations
            WHERE comic_id = ? AND chapter_id = ? AND kind = 'retranslate'
              AND semantic_fingerprint = ?
              AND status IN (
                  'preparing', 'queued', 'running', 'stopping_after_page',
                  'stopping_after_segment', 'paused'
              )
            ORDER BY created_at ASC, rowid ASC LIMIT 1
            """,
            (comic_id, chapter_id, fingerprint),
        )
        if rows:
            generation_id = str(rows[0]["generation_id"])
            if str(rows[0]["status"]) == "paused":
                self.repository.resume(generation_id)
                self._log_task_event(
                    "task_resumed",
                    comic_id=comic_id,
                    chapter_id=chapter_id,
                    generation_id=generation_id,
                    reason="retranslate",
                )
                self._ensure_worker(comic_id, chapter_id)
            return self.repository.task_state(comic_id, chapter_id, generation_id)

        active = self.repository.active_generation(comic_id, chapter_id)
        generation_id = self.repository.create_generation(
            comic_id,
            chapter_id,
            semantic_fingerprint=fingerprint,
            semantic_settings=semantic,
            page_indexes=sorted(source_pages),
            kind="retranslate",
            source_pages=source_pages,
            progressive=True,
        )
        if active is not None:
            self.repository.request_stop(str(active["generation_id"]))
        self._log_task_event(
            "task_queued",
            comic_id=comic_id,
            chapter_id=chapter_id,
            generation_id=generation_id,
            kind="retranslate",
        )
        self._ensure_worker(comic_id, chapter_id)
        return self.repository.task_state(comic_id, chapter_id, generation_id)

    async def retry_page(
        self, comic_id: str, chapter_id: str, page_index: int
    ) -> TranslationTaskState:
        action_lock = self._force_stop_locks.setdefault((comic_id, chapter_id), asyncio.Lock())
        async with action_lock:
            return await self._retry_page(comic_id, chapter_id, page_index)

    async def _retry_page(
        self, comic_id: str, chapter_id: str, page_index: int
    ) -> TranslationTaskState:
        generation = self.repository.latest_generation(comic_id, chapter_id)
        if generation is None:
            raise AppError("TRANSLATION_NOT_FOUND", "本话还没有翻译任务", 404, False)
        generation_id = str(generation["generation_id"])
        page = self.repository.page(generation_id, page_index)
        if page is None:
            raise AppError("PAGE_NOT_FOUND", "翻译页不存在", 404, False)
        if str(page["status"]) != "failed":
            raise AppError("PAGE_NOT_FAILED", "只有失败的图片可以重试", 409, False)
        stage = str(page["error_stage"] or "download")
        paths = self.repository.prepare_retry(
            generation_id,
            page_index,
            clear_columns=page_retry_clear_columns(stage),
        )
        self.cache.delete_entries(paths)
        if str(generation["status"]) not in {
            "queued",
            "running",
            "stopping_after_page",
        }:
            self.repository.resume(generation_id)
        self._ensure_worker(comic_id, chapter_id)
        return self.repository.task_state(comic_id, chapter_id, generation_id)

    async def retry_segment(
        self,
        comic_id: str,
        chapter_id: str,
        page_index: int,
        segment_index: int,
    ) -> TranslationTaskState:
        action_lock = self._force_stop_locks.setdefault((comic_id, chapter_id), asyncio.Lock())
        async with action_lock:
            return await self._retry_segment(
                comic_id,
                chapter_id,
                page_index,
                segment_index,
            )

    async def _retry_segment(
        self,
        comic_id: str,
        chapter_id: str,
        page_index: int,
        segment_index: int,
    ) -> TranslationTaskState:
        generation = self.repository.latest_generation(comic_id, chapter_id)
        if generation is None:
            raise AppError("TRANSLATION_NOT_FOUND", "本话还没有翻译任务", 404, False)
        generation_id = str(generation["generation_id"])
        segment = self.repository.segment(generation_id, page_index, segment_index)
        if segment is None:
            raise AppError("SEGMENT_NOT_FOUND", "翻译分片不存在", 404, False)
        if str(segment["status"]) != "failed":
            raise AppError("SEGMENT_NOT_FAILED", "只有失败的分片可以重试", 409, False)
        stage = str(segment["error_stage"] or "ocr")
        paths = self.repository.prepare_segment_retry(
            generation_id,
            page_index,
            segment_index,
            clear_columns=segment_retry_clear_columns(stage),
            clear_job_id=stage == "ocr",
            increment_attempts=stage != "ocr",
        )
        self.cache.delete_entries(paths)
        if str(generation["status"]) not in {
            "preparing",
            "queued",
            "running",
            "stopping_after_segment",
        }:
            self.repository.resume(generation_id)
        self._ensure_worker(comic_id, chapter_id)
        return self.repository.task_state(comic_id, chapter_id, generation_id)

    async def retry_failed(
        self,
        comic_id: str,
        chapter_id: str,
        *,
        batch_item_id: str | None = None,
    ) -> tuple[TranslationTaskState, int]:
        action_lock = self._force_stop_locks.setdefault((comic_id, chapter_id), asyncio.Lock())
        async with action_lock:
            generation = self.repository.latest_generation(comic_id, chapter_id)
            if generation is None:
                raise AppError("TRANSLATION_NOT_FOUND", "本话还没有翻译任务", 404, False)
            status = str(generation["status"])
            if status in {"stopping_after_page", "stopping_after_segment"}:
                raise AppError(
                    "TRANSLATION_STOPPING",
                    "翻译任务正在停止，请稍后重试",
                    409,
                    True,
                )

            generation_id = str(generation["generation_id"])
            if batch_item_id is not None:
                self.repository.assign_batch_item(
                    generation_id,
                    batch_item_id,
                    comic_id,
                    chapter_id,
                )
            retried_count, paths = self.repository.prepare_failed_retries(generation_id)
            self._log_task_event(
                "retry_failed_requested",
                comic_id=comic_id,
                chapter_id=chapter_id,
                generation_id=generation_id,
                retried_count=retried_count,
            )
            if retried_count:
                try:
                    self.cache.delete_entries(paths)
                except Exception:
                    logger.warning(
                        "Failed to delete invalidated cache entries after retry",
                        extra={"generation_id": generation_id},
                        exc_info=True,
                    )
                if status not in {"preparing", "queued", "running"}:
                    self.repository.resume(generation_id)
                    self._log_task_event(
                        "task_resumed",
                        comic_id=comic_id,
                        chapter_id=chapter_id,
                        generation_id=generation_id,
                        reason="retry_failed",
                    )
                self._wake_generation(generation_id)
                self._ensure_scoped_worker(comic_id, chapter_id, batch_item_id)
            return (
                self.repository.task_state(comic_id, chapter_id, generation_id),
                retried_count,
            )

    def state(self, comic_id: str, chapter_id: str) -> TranslationTaskState:
        return self.repository.task_state(comic_id, chapter_id)

    def decorate_manifest(self, manifest: ChapterManifest) -> ChapterManifest:
        task = self.repository.task_state(manifest.comic_id, manifest.chapter_id)
        task_pages = {page.page_index: page for page in task.pages}
        pages = []
        for page in manifest.pages:
            task_page = task_pages.get(page.index)
            active = self.repository.active_page(manifest.comic_id, manifest.chapter_id, page.index)
            update: dict[str, object] = {}
            if active is not None:
                version = str(active["translated_version"])
                display_parts = self.repository.decode_display_parts(active["display_parts_json"])
                update.update(
                    {
                        "translated_url": self.repository.translated_url(
                            manifest.comic_id,
                            manifest.chapter_id,
                            page.index,
                            version,
                        ),
                        "translated_part_urls": self.repository.translated_part_urls(
                            manifest.comic_id,
                            manifest.chapter_id,
                            page.index,
                            version,
                            len(display_parts),
                        ),
                        "translated_version": version,
                        "width": (int(active["width"]) if active["width"] is not None else None),
                        "height": (int(active["height"]) if active["height"] is not None else None),
                    }
                )
            if task_page is not None:
                update["translation_status"] = task_page.status
                update["error"] = (
                    task_page.error.model_dump(by_alias=True) if task_page.error else None
                )
                update["translation_layers"] = [
                    layer.model_dump(by_alias=True) for layer in task_page.translation_layers
                ]
            pages.append(page.model_copy(update=update))
        return manifest.model_copy(update={"pages": pages})

    def translated_media(
        self,
        comic_id: str,
        chapter_id: str,
        page_index: int,
        version: str,
    ):
        active = self.repository.active_page(comic_id, chapter_id, page_index)
        if active is None or str(active["translated_version"]) != version:
            raise AppError("TRANSLATION_MEDIA_NOT_FOUND", "译图版本不存在", 404, False)
        media = self.cache.read_bytes(
            str(active["translated_path"]),
            media_type="image/png",
            protect=True,
            verify_image=True,
        )
        if media is None:
            raise AppError("TRANSLATION_MEDIA_NOT_FOUND", "译图缓存已失效", 404, True)
        return media

    def translated_part_media(
        self,
        comic_id: str,
        chapter_id: str,
        page_index: int,
        part_index: int,
        version: str,
    ):
        active = self.repository.active_page(comic_id, chapter_id, page_index)
        if active is None or str(active["translated_version"]) != version:
            raise AppError("TRANSLATION_MEDIA_NOT_FOUND", "译图版本不存在", 404, False)
        display_parts = self.repository.decode_display_parts(active["display_parts_json"])
        if part_index >= len(display_parts):
            raise AppError("TRANSLATION_MEDIA_NOT_FOUND", "译图分片不存在", 404, False)
        media = self.cache.read_bytes(
            display_parts[part_index],
            media_type="image/png",
            protect=True,
            verify_image=True,
        )
        if media is None:
            raise AppError("TRANSLATION_MEDIA_NOT_FOUND", "译图分片缓存已失效", 404, True)
        return media

    def translated_segment_media(
        self,
        comic_id: str,
        chapter_id: str,
        page_index: int,
        segment_index: int,
        version: str,
    ):
        active = self.repository.active_segment(
            comic_id,
            chapter_id,
            page_index,
            segment_index,
            version,
        )
        if active is None:
            raise AppError("TRANSLATION_MEDIA_NOT_FOUND", "译图片段版本不存在", 404, False)
        media = self.cache.read_bytes(
            str(active["translated_path"]),
            media_type="image/png",
            protect=True,
            verify_image=True,
        )
        if media is None:
            raise AppError("TRANSLATION_MEDIA_NOT_FOUND", "译图片段缓存已失效", 404, True)
        return media

    def _resume_recovered_workers(self) -> None:
        rows = self.repository.database.fetchall(
            """
            SELECT DISTINCT generations.comic_id, generations.chapter_id
            FROM translation_generations generations
            WHERE generations.status IN ('preparing', 'queued')
              AND NOT EXISTS (
                  SELECT 1
                  FROM translation_batch_items batch_items
                  JOIN translation_batches batches
                    ON batches.batch_id = batch_items.batch_id
                  WHERE batch_items.batch_item_id = generations.batch_item_id
                    AND batches.status NOT IN ('completed', 'cancelled')
              )
            """
        )
        for row in rows:
            self._ensure_worker(str(row["comic_id"]), str(row["chapter_id"]))

    def _ensure_scoped_worker(
        self,
        comic_id: str,
        chapter_id: str,
        batch_item_id: str | None,
    ) -> None:
        if batch_item_id is None:
            self._ensure_worker(comic_id, chapter_id)
        else:
            self._ensure_worker(
                comic_id,
                chapter_id,
                batch_item_id=batch_item_id,
            )

    def _ensure_worker(
        self,
        comic_id: str,
        chapter_id: str,
        *,
        batch_item_id: str | None = None,
    ) -> None:
        key = (comic_id, chapter_id)
        if self._shutting_down or key in self._force_stopping:
            return
        existing = self._tasks.get(key)
        if existing is not None and not existing.done():
            return
        task = asyncio.create_task(
            self._run_chapter(
                comic_id,
                chapter_id,
                batch_item_id=batch_item_id,
            ),
            name=f"translate:{comic_id}:{chapter_id}",
        )
        self._tasks[key] = task
        task.add_done_callback(
            lambda completed, task_key=key: self._worker_done(task_key, completed)
        )
        self._notify_activity_changed()

    def _worker_done(self, key: tuple[str, str], task: asyncio.Task[None]) -> None:
        if self._tasks.get(key) is task:
            self._tasks.pop(key, None)
        if not task.cancelled():
            error = task.exception()
            if error is not None:
                logger.error(
                    "Translation worker stopped unexpectedly",
                    extra={"comic_id": key[0], "chapter_id": key[1]},
                    exc_info=(type(error), error, error.__traceback__),
                )
        self._notify_activity_changed()
        if (
            not self._shutting_down
            and self.repository.next_queued_generation(key[0], key[1]) is not None
        ):
            self._ensure_worker(key[0], key[1])

    async def _run_chapter(
        self,
        comic_id: str,
        chapter_id: str,
        *,
        batch_item_id: str | None = None,
    ) -> None:
        key = (comic_id, chapter_id)
        lock = self._locks.setdefault(key, asyncio.Lock())
        async with lock:
            while generation := self.repository.next_queued_generation(
                comic_id,
                chapter_id,
                batch_item_id=batch_item_id,
            ):
                await self._run_queued_generation(generation, comic_id, chapter_id)

    async def _run_queued_generation(
        self,
        generation: Any,
        comic_id: str,
        chapter_id: str,
    ) -> None:
        generation_id = str(generation["generation_id"])
        with task_log_context(
            generation_id=generation_id,
            comic=comic_id,
            chapter=chapter_id,
        ):
            semantic = self.repository.decode_semantic_settings(generation)
            log_event(
                "task",
                "task_started",
                status=str(generation["status"]),
                kind=str(generation["kind"]),
                pipeline=str(semantic.get("pipelineVersion") or "legacy"),
            )
            self.cache.set_chapter_active(comic_id, chapter_id, True)
            try:
                if semantic.get("pipelineVersion") == PROGRESSIVE_PIPELINE_VERSION:
                    if not self.repository.begin_preparing(generation_id):
                        current = self.repository.generation(generation_id)
                        if current is not None and bool(current["stop_requested"]):
                            self.repository.set_generation_status(
                                generation_id,
                                "paused",
                                stop_requested=False,
                            )
                        return
                    await self._run_streaming_generation(generation_id)
                    return
                if not bool(generation["planning_complete"]):
                    if not self.repository.begin_preparing(generation_id):
                        return
                    prepared = await self._prepare_generation(generation_id)
                    if not prepared:
                        self.repository.set_generation_status(
                            generation_id,
                            "paused",
                            stop_requested=False,
                        )
                        return
                    prepared_generation = self.repository.generation(generation_id)
                    if prepared_generation is None:
                        return
                    if bool(prepared_generation["stop_requested"]):
                        self.repository.set_generation_status(
                            generation_id,
                            "paused",
                            stop_requested=False,
                        )
                        return
                if not self.repository.begin_running(generation_id):
                    return
                await self._run_generation(generation_id)
            except asyncio.CancelledError:
                log_event(
                    "task",
                    "task_cancelled",
                    level=logging.WARNING,
                    reason="worker_cancelled",
                )
                raise
            except Exception as exc:
                code, message = self._classify_error("chapter", exc)
                logger.warning(
                    "Translation generation failed",
                    extra={
                        "generation_id": generation_id,
                        "error_code": code,
                    },
                )
                log_event(
                    "task",
                    "task_failed",
                    level=logging.ERROR,
                    error=code,
                )
                self.repository.set_generation_status(generation_id, "failed", stop_requested=False)
                self.repository.database.execute(
                    """
                    UPDATE translation_pages SET error_stage = 'chapter',
                        error_code = ?, error_summary = ?, updated_at = ?
                    WHERE generation_id = ? AND status = 'pending'
                    """,
                    (
                        code,
                        message,
                        self.repository._timestamp(),
                        generation_id,
                    ),
                )
                self.repository.database.execute(
                    """
                    UPDATE translation_segments SET error_stage = 'chapter',
                        error_code = ?, error_summary = ?, updated_at = ?
                    WHERE generation_id = ? AND status = 'pending'
                    """,
                    (
                        code,
                        message,
                        self.repository._timestamp(),
                        generation_id,
                    ),
                )
            finally:
                self.cache.set_chapter_active(comic_id, chapter_id, False)
                self.cache.enforce_limit()
                self._log_terminal_generation(generation_id)

    async def _run_streaming_generation(self, generation_id: str) -> None:
        generation = self.repository.generation(generation_id)
        if generation is None:
            return
        comic_id = str(generation["comic_id"])
        chapter_id = str(generation["chapter_id"])
        semantic = self.repository.decode_semantic_settings(generation)
        source_pages = await self._generation_source_pages(generation)
        runtime = self._runtime_settings(
            require_services=True,
            translation_service=(
                str(semantic["translationService"]) if semantic.get("translationService") else None
            ),
        )
        pipeline = self._pipeline_factory(semantic, runtime)
        segments_available = asyncio.Event()
        self._generation_events[generation_id] = segments_available

        async def produce() -> bool:
            try:
                return await self._planner.prepare_incrementally(
                    generation_id,
                    comic_id,
                    chapter_id,
                    source_pages,
                    semantic,
                    should_stop=lambda: bool(
                        (current := self.repository.generation(generation_id))
                        and current["stop_requested"]
                    ),
                    on_segments_added=segments_available.set,
                )
            finally:
                segments_available.set()

        producer = asyncio.create_task(
            produce(),
            name=f"prepare:{comic_id}:{chapter_id}",
        )
        consumer = asyncio.create_task(
            self._consume_streaming_segments(
                generation_id,
                comic_id,
                chapter_id,
                pipeline,
                segments_available,
            ),
            name=f"consume:{comic_id}:{chapter_id}",
        )
        try:
            producer_result, consumer_result = await asyncio.gather(
                producer,
                consumer,
            )
        except BaseException:
            producer.cancel()
            consumer.cancel()
            await asyncio.gather(producer, consumer, return_exceptions=True)
            raise
        finally:
            if self._generation_events.get(generation_id) is segments_available:
                self._generation_events.pop(generation_id, None)

        if not producer_result or not consumer_result:
            self.repository.set_generation_status(
                generation_id,
                "paused",
                stop_requested=False,
            )
            return

        finished = self.repository.generation(generation_id)
        if finished is None:
            return
        status = "completed_with_errors" if int(finished["failed_segments"]) > 0 else "completed"
        self.repository.set_generation_status(
            generation_id,
            status,
            stop_requested=False,
        )

    async def _consume_streaming_segments(
        self,
        generation_id: str,
        comic_id: str,
        chapter_id: str,
        pipeline: ImageTranslationPipeline,
        segments_available: asyncio.Event,
    ) -> bool:
        ocr_tasks: dict[tuple[int, int], asyncio.Task[None]] = {}
        current_key: tuple[int, int] | None = None
        try:
            while True:
                segments_available.clear()
                self._discard_finished_ocr_tasks(ocr_tasks)
                current = self.repository.generation(generation_id)
                if current is None:
                    return False

                stopping = bool(current["stop_requested"])
                if stopping:
                    await self._cancel_ocr_tasks(ocr_tasks, keep=current_key)
                    if current_key is None:
                        return False
                else:
                    self._fill_ocr_prefetch(
                        generation_id,
                        comic_id,
                        chapter_id,
                        pipeline,
                        segments_available,
                        ocr_tasks,
                    )

                segment = self.repository.next_unfinished_segment(generation_id)
                if segment is None:
                    if bool(current["planning_complete"]):
                        await self._finalize_progressive_pages(
                            generation_id,
                            comic_id,
                            chapter_id,
                        )
                        self.repository.refresh_counts(generation_id)
                        return True
                    current_key = None
                    await self._wait_for_generation_work(segments_available)
                    continue

                page_index = int(segment["page_index"])
                segment_index = int(segment["segment_index"])
                segment_key = (page_index, segment_index)
                if current_key != segment_key:
                    if not self.repository.begin_segment(
                        generation_id,
                        page_index,
                        segment_index,
                    ):
                        continue
                    current_key = segment_key

                segment = self.repository.segment(generation_id, page_index, segment_index)
                if segment is None:
                    return False
                if str(segment["status"]) == "ocr" or not (
                    segment["ocr_path"] and segment["blocks_path"]
                ):
                    task = ocr_tasks.get(segment_key)
                    if task is None:
                        if stopping:
                            return False
                        self._fill_ocr_prefetch(
                            generation_id,
                            comic_id,
                            chapter_id,
                            pipeline,
                            segments_available,
                            ocr_tasks,
                        )
                        task = ocr_tasks.get(segment_key)
                    if task is None:
                        await self._wait_for_generation_work(segments_available)
                    else:
                        await self._wait_for_ocr_or_generation_work(task, segments_available)
                    refreshed = self.repository.segment(
                        generation_id,
                        page_index,
                        segment_index,
                    )
                    if refreshed is not None and str(refreshed["status"]) == "failed":
                        current_key = None
                        if not self.repository.finish_segment(generation_id):
                            return False
                    continue

                segment_completed = False
                try:
                    await self._segment_runner.complete_after_ocr(
                        generation_id,
                        comic_id,
                        chapter_id,
                        page_index,
                        segment_index,
                        pipeline,
                    )
                    segment_completed = True
                except asyncio.CancelledError:
                    raise
                except Exception as exc:
                    failed = self.repository.segment(
                        generation_id,
                        page_index,
                        segment_index,
                    )
                    stage = str(failed["status"] if failed else "translating")
                    code, message = self._classify_error(stage, exc)
                    self.repository.fail_segment(
                        generation_id,
                        page_index,
                        segment_index,
                        stage=stage,
                        code=code,
                        summary=message,
                    )
                    self.repository.finalize_page_from_segments(generation_id, page_index)

                if segment_completed:
                    await self._segment_runner.publish_page_if_complete(
                        generation_id,
                        comic_id,
                        chapter_id,
                        page_index,
                    )

                current_key = None
                if not self.repository.finish_segment(generation_id):
                    return False
        finally:
            await self._cancel_ocr_tasks(ocr_tasks)

    def _fill_ocr_prefetch(
        self,
        generation_id: str,
        comic_id: str,
        chapter_id: str,
        pipeline: ImageTranslationPipeline,
        segments_available: asyncio.Event,
        ocr_tasks: dict[tuple[int, int], asyncio.Task[None]],
    ) -> None:
        available = self._ocr_limiter.limit - len(ocr_tasks)
        if available <= 0:
            return
        for segment in self.repository.segments_needing_ocr(generation_id):
            key = (int(segment["page_index"]), int(segment["segment_index"]))
            if key in ocr_tasks:
                continue
            task = asyncio.create_task(
                self._prepare_segment_ocr(
                    generation_id,
                    comic_id,
                    chapter_id,
                    key[0],
                    key[1],
                    pipeline,
                    segments_available,
                ),
                name=f"ocr:{comic_id}:{chapter_id}:{key[0]}:{key[1]}",
            )
            ocr_tasks[key] = task
            available -= 1
            if available == 0:
                break

    async def _prepare_segment_ocr(
        self,
        generation_id: str,
        comic_id: str,
        chapter_id: str,
        page_index: int,
        segment_index: int,
        pipeline: ImageTranslationPipeline,
        segments_available: asyncio.Event,
    ) -> None:
        try:
            await self._segment_runner.prepare_ocr(
                generation_id,
                comic_id,
                chapter_id,
                page_index,
                segment_index,
                pipeline,
            )
        except asyncio.CancelledError:
            raise
        except Exception as exc:
            failed = self.repository.segment(generation_id, page_index, segment_index)
            stage = str(failed["status"] if failed else "ocr")
            code, message = self._classify_error(stage, exc)
            self.repository.fail_segment(
                generation_id,
                page_index,
                segment_index,
                stage=stage,
                code=code,
                summary=message,
            )
            self.repository.finalize_page_from_segments(generation_id, page_index)
        finally:
            segments_available.set()

    @staticmethod
    def _discard_finished_ocr_tasks(
        ocr_tasks: dict[tuple[int, int], asyncio.Task[None]],
    ) -> None:
        for key, task in list(ocr_tasks.items()):
            if task.done():
                ocr_tasks.pop(key, None)

    @staticmethod
    async def _cancel_ocr_tasks(
        ocr_tasks: dict[tuple[int, int], asyncio.Task[None]],
        *,
        keep: tuple[int, int] | None = None,
    ) -> None:
        cancelled: list[asyncio.Task[None]] = []
        for key, task in list(ocr_tasks.items()):
            if key == keep:
                continue
            ocr_tasks.pop(key, None)
            if not task.done():
                task.cancel()
            cancelled.append(task)
        if cancelled:
            await asyncio.gather(*cancelled, return_exceptions=True)

    @staticmethod
    async def _wait_for_generation_work(event: asyncio.Event) -> None:
        await event.wait()

    @staticmethod
    async def _wait_for_ocr_or_generation_work(
        ocr_task: asyncio.Task[None],
        event: asyncio.Event,
    ) -> None:
        if ocr_task.done():
            return
        event_waiter = asyncio.create_task(event.wait())
        try:
            await asyncio.wait(
                {ocr_task, event_waiter},
                return_when=asyncio.FIRST_COMPLETED,
            )
        finally:
            if not event_waiter.done():
                event_waiter.cancel()
                await asyncio.gather(event_waiter, return_exceptions=True)

    async def _prepare_generation(self, generation_id: str) -> bool:
        generation = self.repository.generation(generation_id)
        if generation is None:
            return False
        semantic = self.repository.decode_semantic_settings(generation)
        source_pages = await self._generation_source_pages(generation)
        return await self._planner.prepare(
            generation_id,
            str(generation["comic_id"]),
            str(generation["chapter_id"]),
            source_pages,
            semantic,
            should_stop=lambda: bool(
                (current := self.repository.generation(generation_id)) and current["stop_requested"]
            ),
        )

    async def _generation_source_pages(
        self,
        generation: Any,
    ) -> dict[int, str]:
        generation_id = str(generation["generation_id"])
        pages = self.repository.database.fetchall(
            """
            SELECT page_index, source_url FROM translation_pages
            WHERE generation_id = ? ORDER BY page_index
            """,
            (generation_id,),
        )
        source_pages = {
            int(row["page_index"]): str(row["source_url"]) for row in pages if row["source_url"]
        }
        if len(source_pages) != int(generation["total_pages"]):
            source_pages = await self._ensure_source_pages(
                str(generation["comic_id"]),
                str(generation["chapter_id"]),
            )
        return source_pages

    async def _run_generation(self, generation_id: str) -> None:
        generation = self.repository.generation(generation_id)
        if generation is None:
            return
        comic_id = str(generation["comic_id"])
        chapter_id = str(generation["chapter_id"])
        semantic = self.repository.decode_semantic_settings(generation)
        runtime = self._runtime_settings(
            require_services=True,
            translation_service=(
                str(semantic["translationService"]) if semantic.get("translationService") else None
            ),
        )
        pipeline = self._pipeline_factory(semantic, runtime)

        if semantic.get("pipelineVersion") in PROGRESSIVE_PIPELINE_VERSIONS:
            await self._run_progressive_generation(
                generation_id,
                comic_id,
                chapter_id,
                pipeline,
            )
            return

        await self._run_legacy_generation(
            generation_id,
            comic_id,
            chapter_id,
            pipeline,
        )

    async def _run_progressive_generation(
        self,
        generation_id: str,
        comic_id: str,
        chapter_id: str,
        pipeline: ImageTranslationPipeline,
    ) -> None:
        while True:
            current = self.repository.generation(generation_id)
            if current is None:
                return
            if bool(current["stop_requested"]) and current["current_segment_index"] is None:
                self.repository.set_generation_status(
                    generation_id,
                    "paused",
                    stop_requested=False,
                )
                return
            pending = self.repository.pending_segments(generation_id)
            if not pending:
                await self._finalize_progressive_pages(
                    generation_id,
                    comic_id,
                    chapter_id,
                )
                self.repository.refresh_counts(generation_id)
                finished = self.repository.generation(generation_id)
                if finished is None:
                    return
                status = (
                    "completed_with_errors" if int(finished["failed_segments"]) > 0 else "completed"
                )
                self.repository.set_generation_status(
                    generation_id,
                    status,
                    stop_requested=False,
                )
                return

            segment = pending[0]
            page_index = int(segment["page_index"])
            segment_index = int(segment["segment_index"])
            self.repository.set_generation_status(
                generation_id,
                "running",
                current_page_index=page_index,
                current_segment_index=segment_index,
            )
            segment_completed = False
            try:
                await self._segment_runner.process(
                    generation_id,
                    comic_id,
                    chapter_id,
                    page_index,
                    segment_index,
                    pipeline,
                )
                segment_completed = True
            except asyncio.CancelledError:
                raise
            except Exception as exc:
                failed = self.repository.segment(generation_id, page_index, segment_index)
                stage = str(failed["status"] if failed else "ocr")
                code, message = self._classify_error(stage, exc)
                self.repository.fail_segment(
                    generation_id,
                    page_index,
                    segment_index,
                    stage=stage,
                    code=code,
                    summary=message,
                )
                self.repository.finalize_page_from_segments(generation_id, page_index)

            if segment_completed:
                await self._segment_runner.publish_page_if_complete(
                    generation_id,
                    comic_id,
                    chapter_id,
                    page_index,
                )

            after_segment = self.repository.generation(generation_id)
            if after_segment is None:
                return
            if bool(after_segment["stop_requested"]):
                self.repository.set_generation_status(
                    generation_id,
                    "paused",
                    stop_requested=False,
                )
                return
            self.repository.set_generation_status(
                generation_id,
                "running",
                current_page_index=None,
                current_segment_index=None,
            )

    async def _finalize_progressive_pages(
        self,
        generation_id: str,
        comic_id: str,
        chapter_id: str,
    ) -> None:
        pages = self.repository.database.fetchall(
            """
            SELECT page_index, status FROM translation_pages
            WHERE generation_id = ? ORDER BY page_index
            """,
            (generation_id,),
        )
        for page in pages:
            page_index = int(page["page_index"])
            result = self.repository.finalize_page_from_segments(
                generation_id,
                page_index,
            )
            if result == "ready" and str(page["status"]) != "completed":
                await self._segment_runner.publish_page_if_complete(
                    generation_id,
                    comic_id,
                    chapter_id,
                    page_index,
                )

    async def _run_legacy_generation(
        self,
        generation_id: str,
        comic_id: str,
        chapter_id: str,
        pipeline: ImageTranslationPipeline,
    ) -> None:
        while True:
            current = self.repository.generation(generation_id)
            if current is None:
                return
            if bool(current["stop_requested"]) and current["current_page_index"] is None:
                self.repository.set_generation_status(generation_id, "paused", stop_requested=False)
                return
            pending = self.repository.pending_pages(generation_id)
            if not pending:
                self.repository.refresh_counts(generation_id)
                finished = self.repository.generation(generation_id)
                if finished is None:
                    return
                status = (
                    "completed_with_errors" if int(finished["failed_pages"]) > 0 else "completed"
                )
                self.repository.set_generation_status(generation_id, status, stop_requested=False)
                return

            page_index = int(pending[0]["page_index"])
            self.repository.set_generation_status(
                generation_id, "running", current_page_index=page_index
            )
            try:
                await self._process_page(
                    generation_id,
                    comic_id,
                    chapter_id,
                    page_index,
                    pipeline,
                )
            except asyncio.CancelledError:
                raise
            except Exception as exc:
                page = self.repository.page(generation_id, page_index)
                stage = str(page["status"] if page else "download")
                code, message = self._classify_error(stage, exc)
                self.repository.fail_page(
                    generation_id,
                    page_index,
                    stage=stage,
                    code=code,
                    summary=message,
                )

            after_page = self.repository.generation(generation_id)
            if after_page is None:
                return
            if bool(after_page["stop_requested"]):
                self.repository.set_generation_status(
                    generation_id,
                    "paused",
                    stop_requested=False,
                    current_page_index=None,
                )
                return
            self.repository.set_generation_status(generation_id, "running", current_page_index=None)

    async def _process_page(
        self,
        generation_id: str,
        comic_id: str,
        chapter_id: str,
        page_index: int,
        pipeline: ImageTranslationPipeline,
    ) -> None:
        with task_log_context(
            generation_id=generation_id,
            comic=comic_id,
            chapter=chapter_id,
            page_index=page_index,
        ):
            await self._process_page_in_context(
                generation_id,
                comic_id,
                chapter_id,
                page_index,
                pipeline,
            )

    async def _process_page_in_context(
        self,
        generation_id: str,
        comic_id: str,
        chapter_id: str,
        page_index: int,
        pipeline: ImageTranslationPipeline,
    ) -> None:
        bundle_key = chapter_bundle_key(comic_id, chapter_id)
        source_url = self.registry.pages.get((comic_id, chapter_id, page_index))
        if source_url is None:
            raise AppError("SOURCE_PAGE_MISSING", "源图片地址不存在", 502, True)

        page = self.repository.page(generation_id, page_index)
        assert page is not None
        original_relative = str(
            page["original_path"] or original_path(comic_id, chapter_id, page_index)
        )
        self.repository.set_page_stage(
            generation_id,
            page_index,
            "downloading",
            increment_attempts=True,
        )
        with logged_stage("download") as download_summary:
            original = self.cache.read_bytes(
                original_relative,
                media_type="image/png",
                protect=True,
                verify_image=True,
            )
            original_cached = original is not None
            log_event(
                "task",
                "cache_hit" if original_cached else "cache_miss",
                artifact="original_image",
            )
            if original is None:
                original = await self.cache.get_or_create(
                    bundle_key=bundle_key,
                    bundle_kind="chapter",
                    comic_id=comic_id,
                    chapter_id=chapter_id,
                    relative_path=original_relative,
                    entry_kind="original",
                    loader=lambda: self.source.fetch_media(source_url),
                    protect=True,
                )
            download_summary.update(
                cached=original_cached,
                output_bytes=len(original.content),
            )
        self.repository.set_page_stage(
            generation_id,
            page_index,
            "ocr",
            paths={"original_path": original_relative},
        )

        page = self.repository.page(generation_id, page_index)
        assert page is not None
        ocr_path = str(
            page["ocr_path"]
            or generation_page_path(comic_id, chapter_id, generation_id, "ocr", page_index, "json")
        )
        blocks_path = str(
            page["blocks_path"]
            or generation_page_path(
                comic_id, chapter_id, generation_id, "blocks", page_index, "json"
            )
        )
        with logged_stage("ocr", input_bytes=len(original.content)) as ocr_summary:
            ocr_media = self.cache.read_bytes(ocr_path, verify_image=False)
            blocks_media = self.cache.read_bytes(blocks_path, verify_image=False)
            log_event(
                "task",
                "cache_hit" if ocr_media is not None else "cache_miss",
                artifact="ocr",
            )
            log_event(
                "task",
                "cache_hit" if blocks_media is not None else "cache_miss",
                artifact="blocks",
            )
            ocr_cached = ocr_media is not None and blocks_media is not None
            if ocr_cached:
                block_values = json.loads(blocks_media.content)
                blocks = [TextBlock.from_dict(item) for item in block_values]
                image, _sanitized = await asyncio.to_thread(sanitize_image, original.content)
                ocr_bytes = len(ocr_media.content) + len(blocks_media.content)
            else:
                ocr_output = await pipeline.run_ocr(original.content)
                image = ocr_output.image
                blocks = ocr_output.blocks
                ocr_bytes = self._put_json(
                    bundle_key,
                    comic_id,
                    chapter_id,
                    ocr_path,
                    "ocr",
                    ocr_output.payload,
                )
                ocr_bytes += self._put_json(
                    bundle_key,
                    comic_id,
                    chapter_id,
                    blocks_path,
                    "blocks",
                    [block.as_dict() for block in blocks],
                )
            ocr_summary.update(
                cached=ocr_cached,
                blocks=len(blocks),
                output_bytes=ocr_bytes,
            )
        self.repository.set_page_stage(
            generation_id,
            page_index,
            "translating",
            paths={"ocr_path": ocr_path, "blocks_path": blocks_path},
        )

        page = self.repository.page(generation_id, page_index)
        assert page is not None
        translations_path = str(
            page["translations_path"]
            or generation_page_path(
                comic_id,
                chapter_id,
                generation_id,
                "translations",
                page_index,
                "json",
            )
        )
        with logged_stage("translation", blocks=len(blocks)) as translation_summary:
            translations_media = self.cache.read_bytes(translations_path, verify_image=False)
            log_event(
                "task",
                "cache_hit" if translations_media is not None else "cache_miss",
                artifact="translations",
            )
            if translations_media is not None:
                translation_values = json.loads(translations_media.content)
                translated_blocks = [TextBlock.from_dict(item) for item in translation_values]
                translations_bytes = len(translations_media.content)
            else:
                translation_output = await pipeline.translate_blocks(blocks)
                translated_blocks = translation_output.blocks
                translations_bytes = self._put_json(
                    bundle_key,
                    comic_id,
                    chapter_id,
                    translations_path,
                    "translations",
                    [block.as_dict() for block in translated_blocks],
                )
            translation_summary.update(
                cached=translations_media is not None,
                translated=sum(block.translation is not None for block in translated_blocks),
                output_bytes=translations_bytes,
            )
        self.repository.set_page_stage(
            generation_id,
            page_index,
            "rendering",
            paths={"translations_path": translations_path},
        )

        translated_path = generation_page_path(
            comic_id,
            chapter_id,
            generation_id,
            "translated",
            page_index,
            "png",
        )
        translated_media = self.cache.read_bytes(
            translated_path,
            media_type="image/png",
            verify_image=True,
        )
        render_cached = translated_media is not None
        log_event(
            "task",
            "cache_hit" if render_cached else "cache_miss",
            artifact="translated_image",
        )
        with logged_stage(
            "render",
            blocks=len(translated_blocks),
            input_bytes=len(original.content),
        ) as render_summary:
            display_paths: list[str] = []
            if translated_media is None:
                render_output = await pipeline.render(image, translated_blocks)
                translated_media = self.cache.put_bytes(
                    bundle_key=bundle_key,
                    bundle_kind="chapter",
                    comic_id=comic_id,
                    chapter_id=chapter_id,
                    relative_path=translated_path,
                    entry_kind="translated",
                    content=render_output.translated_bytes,
                    media_type="image/png",
                    verify_image=True,
                )
                width, height = render_output.width, render_output.height
                for part_index, content in enumerate(render_output.display_parts):
                    part_path = generation_page_path(
                        comic_id,
                        chapter_id,
                        generation_id,
                        f"display-parts/{page_index:05d}",
                        part_index,
                        "png",
                    )
                    self.cache.put_bytes(
                        bundle_key=bundle_key,
                        bundle_kind="chapter",
                        comic_id=comic_id,
                        chapter_id=chapter_id,
                        relative_path=part_path,
                        entry_kind="display_part",
                        content=content,
                        media_type="image/png",
                        verify_image=True,
                    )
                    display_paths.append(part_path)
            else:
                with Image.open(io.BytesIO(translated_media.content)) as translated_image:
                    width, height = translated_image.size
                rows = self.repository.database.fetchall(
                    """
                    SELECT relative_path FROM cache_entries
                    WHERE bundle_key = ? AND entry_kind = 'display_part'
                      AND relative_path LIKE ? ORDER BY relative_path
                    """,
                    (
                        bundle_key,
                        f"%/display-parts/{page_index:05d}/%",
                    ),
                )
                display_paths = [str(row["relative_path"]) for row in rows]

            self.repository.complete_page(
                generation_id,
                comic_id,
                chapter_id,
                page_index,
                translated_path=translated_path,
                translated_version=translated_media.etag,
                width=width,
                height=height,
                display_parts=display_paths,
            )
            render_summary.update(
                cached=render_cached,
                output_bytes=len(translated_media.content),
                display_parts=len(display_paths),
            )

    async def _ensure_source_pages(self, comic_id: str, chapter_id: str) -> dict[int, str]:
        pages = {
            index: url
            for (registered_comic, registered_chapter, index), url in self.registry.pages.items()
            if registered_comic == comic_id and registered_chapter == chapter_id
        }
        if not pages:
            source_manifest = await self.source.chapter(comic_id, chapter_id)
            self.registry.localize_manifest(source_manifest)
            pages = {
                index: url
                for (
                    registered_comic,
                    registered_chapter,
                    index,
                ), url in self.registry.pages.items()
                if registered_comic == comic_id and registered_chapter == chapter_id
            }
        if not pages:
            raise AppError("CHAPTER_EMPTY", "章节没有可翻译的源图片", 502, True)
        return pages

    def _runtime_settings(
        self,
        *,
        require_services: bool,
        translation_service: str | None = None,
    ) -> dict[str, Any]:
        values = self.settings.values(include_secrets=True)
        if require_services:
            if not str(values.get("ocr_api_url") or "").strip():
                raise AppError("OCR_NOT_CONFIGURED", "请先在设置中配置 OCR 接口", 409, False)
            self._require_ocr_auth(values)
            self._require_translation_service(
                values,
                translation_service or str(values.get("translation_service") or "deepl"),
            )
        return values

    @staticmethod
    def _require_ocr_auth(values: dict[str, Any]) -> None:
        auth_mode = str(values.get("ocr_auth_mode") or "none").strip().lower()
        if auth_mode == "none":
            return
        if auth_mode == "bearer":
            if not str(values.get("ocr_token") or "").strip():
                raise AppError(
                    "OCR_AUTH_NOT_CONFIGURED",
                    "请先配置 OCR Token",
                    409,
                    False,
                )
            return
        if auth_mode == "basic":
            if (
                not str(values.get("ocr_basic_username") or "").strip()
                or not str(values.get("ocr_basic_password") or "").strip()
            ):
                raise AppError(
                    "OCR_AUTH_NOT_CONFIGURED",
                    "请先配置 OCR Basic Auth 用户名和密码",
                    409,
                    False,
                )
            return
        raise AppError("OCR_AUTH_INVALID", "OCR 鉴权模式设置无效", 409, False)

    @staticmethod
    def _require_translation_service(values: dict[str, Any], service: str) -> None:
        if service == "deepl":
            if not str(values.get("deepl_api_key") or "").strip():
                raise AppError(
                    "TRANSLATOR_NOT_CONFIGURED",
                    "请先在设置中配置 DeepL API Key",
                    409,
                    False,
                )
            return
        if service == "deeplx":
            if not str(values.get("deeplx_url") or "").strip():
                raise AppError(
                    "TRANSLATOR_NOT_CONFIGURED",
                    "请先在设置中配置 DeepLX 接口",
                    409,
                    False,
                )
            return
        raise AppError("TRANSLATOR_INVALID", "翻译服务设置无效", 409, False)

    def _semantic_settings(
        self, source_pages: dict[int, str], runtime: dict[str, Any]
    ) -> tuple[dict[str, object], str]:
        ocr_protocol = resolve_ocr_protocol(
            str(runtime.get("ocr_mode") or "auto"),
            str(runtime.get("ocr_api_url") or ""),
        )
        semantic: dict[str, object] = {
            "sourcePages": [
                {"index": index, "identity": hashlib.sha256(url.encode()).hexdigest()}
                for index, url in sorted(source_pages.items())
            ],
            "sourceLanguage": runtime["source_language"],
            "targetLanguage": "ZH-HANS",
            "ocrModel": runtime["ocr_model"],
            "ocrProtocol": ocr_protocol,
            "translationService": runtime["translation_service"],
            "longImageThreshold": runtime["long_image_threshold"],
            "ocrSliceHeight": runtime["ocr_slice_height"],
            "ocrSliceOverlap": runtime["ocr_slice_overlap"],
            "readingSliceHeight": runtime["reading_slice_height"],
            "longImageAspectRatio": 2.6,
            "pipelineVersion": PROGRESSIVE_PIPELINE_VERSION,
            "ocrOptionsVersion": "text-block-ocr-v2",
            "rendererVersion": RENDERER_VERSION,
            "fontIdentity": font_identity(),
        }
        serialized = json.dumps(
            semantic,
            ensure_ascii=False,
            separators=(",", ":"),
            sort_keys=True,
        ).encode("utf-8")
        return semantic, hashlib.sha256(serialized).hexdigest()

    def _build_pipeline(
        self, semantic: dict[str, Any], runtime: dict[str, Any]
    ) -> ImageTranslationPipeline:
        ocr = OCRClient(
            self._http_client,
            str(runtime["ocr_api_url"]),
            token=str(runtime.get("ocr_token") or ""),
            auth_mode=str(runtime.get("ocr_auth_mode") or "none"),
            basic_username=str(runtime.get("ocr_basic_username") or ""),
            basic_password=str(runtime.get("ocr_basic_password") or ""),
            mode=str(runtime["ocr_mode"]),
            job_model=str(semantic.get("ocrModel") or "PaddleOCR-VL-1.6"),
            job_poll_interval=float(runtime["ocr_poll_interval_seconds"]),
            job_timeout=float(runtime["ocr_timeout_seconds"]),
            concurrency=int(runtime["ocr_concurrency"]),
            request_timeout=float(runtime["ocr_timeout_seconds"]),
            limiter=self._ocr_limiter,
        )
        service = str(semantic.get("translationService") or runtime["translation_service"])
        self._require_translation_service(runtime, service)
        if service == "deepl":
            translator = DeepLClient(
                self._http_client,
                str(runtime["deepl_api_key"]),
                concurrency=int(runtime["translation_concurrency"]),
                timeout=float(runtime["translation_timeout_seconds"]),
            )
        else:
            translator = DeepLXClient(
                self._http_client,
                str(runtime["deeplx_url"]),
                concurrency=int(runtime["translation_concurrency"]),
                timeout=float(runtime["translation_timeout_seconds"]),
            )
        return ImageTranslationPipeline(
            ocr,
            translator,
            PipelineSettings(
                source_language=str(semantic["sourceLanguage"]),
                long_image_threshold=int(semantic["longImageThreshold"]),
                ocr_slice_height=int(semantic["ocrSliceHeight"]),
                ocr_slice_overlap=int(semantic["ocrSliceOverlap"]),
                reading_slice_height=int(semantic["readingSliceHeight"]),
            ),
        )

    def _put_json(
        self,
        bundle_key: str,
        comic_id: str,
        chapter_id: str,
        relative_path: str,
        entry_kind: str,
        value: object,
    ) -> int:
        content = json.dumps(
            value,
            ensure_ascii=False,
            separators=(",", ":"),
        ).encode("utf-8")
        self.cache.put_bytes(
            bundle_key=bundle_key,
            bundle_kind="chapter",
            comic_id=comic_id,
            chapter_id=chapter_id,
            relative_path=relative_path,
            entry_kind=entry_kind,
            content=content,
            media_type="application/json",
            verify_image=False,
        )
        return len(content)

    @staticmethod
    def _classify_error(stage: str, error: Exception) -> tuple[str, str]:
        stage_name = {
            "downloading": "download",
            "ocr": "ocr",
            "translating": "translation",
            "rendering": "render",
        }.get(stage, stage)
        if isinstance(error, TimeoutError | httpx.TimeoutException):
            return f"{stage_name.upper()}_TIMEOUT", f"{stage_name} 接口超时"
        if isinstance(error, AppError):
            return error.code, error.message
        if isinstance(error, OCRJobFailedError):
            return "OCR_JOB_FAILED", str(error)
        if isinstance(error, OCRJobNotFoundError):
            return "OCR_JOB_NOT_FOUND", str(error)
        if isinstance(error, OCRProtocolError):
            return "OCR_PROTOCOL_ERROR", str(error)
        if isinstance(error, DeepLAuthenticationError):
            return "DEEPL_AUTH_ERROR", "DeepL API Key 无效或没有访问权限"
        if isinstance(error, DeepLQuotaExceededError):
            return "DEEPL_QUOTA_EXCEEDED", "DeepL API 配额已用尽"
        if isinstance(error, DeepLRateLimitError):
            return "DEEPL_RATE_LIMITED", "DeepL API 请求过于频繁"
        if isinstance(error, TranslationInputTooLargeError):
            return "TRANSLATION_INPUT_TOO_LARGE", str(error)
        if isinstance(error, TranslationProtocolError):
            return "TRANSLATION_PROTOCOL_ERROR", str(error)
        if isinstance(error, httpx.HTTPStatusError):
            return (
                f"{stage_name.upper()}_HTTP_ERROR",
                f"{stage_name} 接口返回 HTTP {error.response.status_code}",
            )
        if isinstance(error, httpx.HTTPError):
            return f"{stage_name.upper()}_NETWORK_ERROR", f"{stage_name} 网络错误"
        return f"{stage_name.upper()}_FAILED", f"{stage_name} 处理失败"
