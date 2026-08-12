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
from app.domain.translation import TranslationTaskState
from app.errors import AppError
from app.media.registry import SourceMediaRegistry
from app.repositories.translation import TranslationRepository
from app.sources.base import ComicSource
from app.translation.image_renderer import (
    RENDERER_VERSION,
    font_identity,
    sanitize_image,
)
from app.translation.models import TextBlock
from app.translation.ocr import OCRClient, OCRJobFailedError, OCRProtocolError
from app.translation.pipeline import ImageTranslationPipeline, PipelineSettings
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
        self._http_client = httpx.AsyncClient(timeout=httpx.Timeout(60.0))
        self._pipeline_factory = pipeline_factory or self._build_pipeline
        self.repository.recover_interrupted()
        self.repository.recover_invalid_checkpoints()

    async def shutdown(self) -> None:
        active_tasks = [task for task in self._tasks.values() if not task.done()]
        for task in active_tasks:
            task.cancel()
        if active_tasks:
            await asyncio.gather(*active_tasks, return_exceptions=True)
        self.repository.recover_interrupted()
        await self._http_client.aclose()

    async def start(self, comic_id: str, chapter_id: str) -> TranslationTaskState:
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
            self.repository.resume(generation_id)
            self._ensure_worker(comic_id, chapter_id)
            return self.repository.task_state(comic_id, chapter_id, generation_id)
        matching = self.repository.matching_generation(comic_id, chapter_id, fingerprint)

        if active is not None and str(active["semantic_fingerprint"]) == fingerprint:
            if bool(active["stop_requested"]):
                self.repository.set_generation_status(
                    str(active["generation_id"]),
                    "running",
                    stop_requested=False,
                    current_page_index=(
                        int(active["current_page_index"])
                        if active["current_page_index"] is not None
                        else None
                    ),
                )
            self._ensure_worker(comic_id, chapter_id)
            return self.repository.task_state(comic_id, chapter_id, str(active["generation_id"]))

        if matching is not None:
            generation_id = str(matching["generation_id"])
            status = str(matching["status"])
            if status == "paused":
                self.repository.resume(generation_id)
            elif status in {"completed", "completed_with_errors"}:
                return self.repository.task_state(comic_id, chapter_id, generation_id)
            elif status == "failed":
                self.repository.resume(generation_id)
            if active is not None and str(active["generation_id"]) != generation_id:
                self.repository.request_stop(str(active["generation_id"]))
            self._ensure_worker(comic_id, chapter_id)
            return self.repository.task_state(comic_id, chapter_id, generation_id)

        generation_id = self.repository.create_generation(
            comic_id,
            chapter_id,
            semantic_fingerprint=fingerprint,
            semantic_settings=semantic,
            page_indexes=sorted(source_pages),
            kind="normal",
        )
        if active is not None:
            self.repository.request_stop(str(active["generation_id"]))
        self._ensure_worker(comic_id, chapter_id)
        return self.repository.task_state(comic_id, chapter_id, generation_id)

    async def pause(self, comic_id: str, chapter_id: str) -> TranslationTaskState:
        active_rows = self.repository.database.fetchall(
            """
            SELECT generation_id FROM translation_generations
            WHERE comic_id = ? AND chapter_id = ?
              AND status IN ('queued', 'running', 'stopping_after_page')
            ORDER BY created_at ASC, generation_id ASC
            """,
            (comic_id, chapter_id),
        )
        if not active_rows:
            return self.repository.task_state(comic_id, chapter_id)
        for row in active_rows:
            self.repository.request_stop(str(row["generation_id"]))
        latest_id = str(active_rows[-1]["generation_id"])
        return self.repository.task_state(comic_id, chapter_id, latest_id)

    async def retranslate(self, comic_id: str, chapter_id: str) -> TranslationTaskState:
        source_pages = await self._ensure_source_pages(comic_id, chapter_id)
        runtime = self._runtime_settings(require_services=True)
        semantic, fingerprint = self._semantic_settings(source_pages, runtime)
        rows = self.repository.database.fetchall(
            """
            SELECT * FROM translation_generations
            WHERE comic_id = ? AND chapter_id = ? AND kind = 'retranslate'
              AND status IN ('queued', 'running', 'stopping_after_page', 'paused')
            ORDER BY created_at ASC LIMIT 1
            """,
            (comic_id, chapter_id),
        )
        if rows:
            generation_id = str(rows[0]["generation_id"])
            if str(rows[0]["status"]) == "paused":
                self.repository.resume(generation_id)
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
        )
        if active is not None:
            self.repository.request_stop(str(active["generation_id"]))
        self._ensure_worker(comic_id, chapter_id)
        return self.repository.task_state(comic_id, chapter_id, generation_id)

    async def retry_page(
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
        clear_by_stage = {
            "download": [
                "original_path",
                "ocr_path",
                "blocks_path",
                "translations_path",
                "translated_path",
            ],
            "downloading": [
                "original_path",
                "ocr_path",
                "blocks_path",
                "translations_path",
                "translated_path",
            ],
            "ocr": [
                "ocr_path",
                "blocks_path",
                "translations_path",
                "translated_path",
            ],
            "translation": ["translations_path", "translated_path"],
            "translating": ["translations_path", "translated_path"],
            "render": ["translated_path"],
            "rendering": ["translated_path"],
        }
        paths = self.repository.prepare_retry(
            generation_id,
            page_index,
            clear_columns=clear_by_stage.get(stage, ["translated_path"]),
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

    def _ensure_worker(self, comic_id: str, chapter_id: str) -> None:
        key = (comic_id, chapter_id)
        existing = self._tasks.get(key)
        if existing is not None and not existing.done():
            return
        task = asyncio.create_task(
            self._run_chapter(comic_id, chapter_id),
            name=f"translate:{comic_id}:{chapter_id}",
        )
        self._tasks[key] = task
        task.add_done_callback(
            lambda completed, task_key=key: self._worker_done(task_key, completed)
        )

    def _worker_done(self, key: tuple[str, str], task: asyncio.Task[None]) -> None:
        if self._tasks.get(key) is task:
            self._tasks.pop(key, None)
        if task.cancelled():
            return
        error = task.exception()
        if error is not None:
            logger.error(
                "Translation worker stopped unexpectedly",
                extra={"comic_id": key[0], "chapter_id": key[1]},
                exc_info=(type(error), error, error.__traceback__),
            )

    async def _run_chapter(self, comic_id: str, chapter_id: str) -> None:
        key = (comic_id, chapter_id)
        lock = self._locks.setdefault(key, asyncio.Lock())
        async with lock:
            while generation := self.repository.next_queued_generation(comic_id, chapter_id):
                generation_id = str(generation["generation_id"])
                self.repository.set_generation_status(
                    generation_id, "running", stop_requested=False
                )
                self.cache.set_chapter_active(comic_id, chapter_id, True)
                try:
                    await self._run_generation(generation_id)
                except asyncio.CancelledError:
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
                    self.repository.set_generation_status(
                        generation_id, "failed", stop_requested=False
                    )
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
                finally:
                    self.cache.set_chapter_active(comic_id, chapter_id, False)
                    self.cache.enforce_limit()

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
        original = self.cache.read_bytes(
            original_relative,
            media_type="image/png",
            protect=True,
            verify_image=True,
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
        ocr_media = self.cache.read_bytes(ocr_path, verify_image=False)
        blocks_media = self.cache.read_bytes(blocks_path, verify_image=False)
        if ocr_media is not None and blocks_media is not None:
            block_values = json.loads(blocks_media.content)
            blocks = [TextBlock.from_dict(item) for item in block_values]
            image, _sanitized = await asyncio.to_thread(sanitize_image, original.content)
        else:
            ocr_output = await pipeline.run_ocr(original.content)
            image = ocr_output.image
            blocks = ocr_output.blocks
            self._put_json(
                bundle_key,
                comic_id,
                chapter_id,
                ocr_path,
                "ocr",
                ocr_output.payload,
            )
            self._put_json(
                bundle_key,
                comic_id,
                chapter_id,
                blocks_path,
                "blocks",
                [block.as_dict() for block in blocks],
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
        translations_media = self.cache.read_bytes(translations_path, verify_image=False)
        if translations_media is not None:
            translation_values = json.loads(translations_media.content)
            translated_blocks = [TextBlock.from_dict(item) for item in translation_values]
        else:
            translation_output = await pipeline.translate_blocks(blocks)
            translated_blocks = translation_output.blocks
            self._put_json(
                bundle_key,
                comic_id,
                chapter_id,
                translations_path,
                "translations",
                [block.as_dict() for block in translated_blocks],
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
            if not str(values.get("ocr_token") or "").strip():
                raise AppError("OCR_AUTH_NOT_CONFIGURED", "请先配置 OCR Token", 409, False)
            self._require_translation_service(
                values,
                translation_service or str(values.get("translation_service") or "deepl"),
            )
        return values

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
        semantic: dict[str, object] = {
            "sourcePages": [
                {"index": index, "identity": hashlib.sha256(url.encode()).hexdigest()}
                for index, url in sorted(source_pages.items())
            ],
            "sourceLanguage": runtime["source_language"],
            "targetLanguage": "ZH-HANS",
            "ocrModel": runtime["ocr_model"],
            "translationService": runtime["translation_service"],
            "longImageThreshold": runtime["long_image_threshold"],
            "ocrSliceHeight": runtime["ocr_slice_height"],
            "ocrSliceOverlap": runtime["ocr_slice_overlap"],
            "readingSliceHeight": runtime["reading_slice_height"],
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
            job_model=str(semantic.get("ocrModel") or "PaddleOCR-VL-1.6"),
            job_poll_interval=float(runtime["ocr_poll_interval_seconds"]),
            job_timeout=float(runtime["ocr_timeout_seconds"]),
            concurrency=int(runtime["ocr_concurrency"]),
            request_timeout=float(runtime["ocr_timeout_seconds"]),
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
    ) -> None:
        self.cache.put_bytes(
            bundle_key=bundle_key,
            bundle_kind="chapter",
            comic_id=comic_id,
            chapter_id=chapter_id,
            relative_path=relative_path,
            entry_kind=entry_kind,
            content=json.dumps(
                value,
                ensure_ascii=False,
                separators=(",", ":"),
            ).encode("utf-8"),
            media_type="application/json",
            verify_image=False,
        )

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
