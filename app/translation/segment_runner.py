from __future__ import annotations

import asyncio
import hashlib
import json
from typing import Any

from PIL import Image

from app.cache.keys import chapter_bundle_key, generation_segment_path
from app.cache.storage import MediaCache
from app.observability import log_event, logged_stage, task_log_context
from app.repositories.translation import TranslationRepository
from app.sources.base import ComicSource
from app.translation.image_renderer import image_to_png_bytes, sanitize_image
from app.translation.models import TextBlock
from app.translation.ocr import OCRJobNotFoundError
from app.translation.pipeline import ImageTranslationPipeline, OCROutput


class SegmentRunner:
    def __init__(
        self,
        *,
        repository: TranslationRepository,
        cache: MediaCache,
        source: ComicSource,
    ) -> None:
        self.repository = repository
        self.cache = cache
        self.source = source

    async def process(
        self,
        generation_id: str,
        comic_id: str,
        chapter_id: str,
        page_index: int,
        segment_index: int,
        pipeline: ImageTranslationPipeline,
    ) -> None:
        await self.prepare_ocr(
            generation_id,
            comic_id,
            chapter_id,
            page_index,
            segment_index,
            pipeline,
        )
        await self.complete_after_ocr(
            generation_id,
            comic_id,
            chapter_id,
            page_index,
            segment_index,
            pipeline,
        )

    async def prepare_ocr(
        self,
        generation_id: str,
        comic_id: str,
        chapter_id: str,
        page_index: int,
        segment_index: int,
        pipeline: ImageTranslationPipeline,
    ) -> None:
        segment = self.repository.segment(generation_id, page_index, segment_index)
        global_index = int(segment["global_index"]) if segment is not None else None
        with (
            task_log_context(
                generation_id=generation_id,
                comic=comic_id,
                chapter=chapter_id,
                page_index=page_index,
                segment_index=segment_index,
                global_index=global_index,
            ),
            logged_stage("ocr") as summary,
        ):
            summary.update(
                await self._prepare_ocr_in_context(
                    generation_id,
                    comic_id,
                    chapter_id,
                    page_index,
                    segment_index,
                    pipeline,
                )
            )

    async def _prepare_ocr_in_context(
        self,
        generation_id: str,
        comic_id: str,
        chapter_id: str,
        page_index: int,
        segment_index: int,
        pipeline: ImageTranslationPipeline,
    ) -> dict[str, object]:
        segment = self.repository.segment(generation_id, page_index, segment_index)
        if segment is None:
            raise ValueError("translation segment does not exist")
        if not self.repository.claim_segment_ocr(generation_id, page_index, segment_index):
            raise RuntimeError("translation segment is not available for OCR")
        bundle_key = chapter_bundle_key(comic_id, chapter_id)
        try:
            segment = self.repository.segment(generation_id, page_index, segment_index)
            assert segment is not None
            ocr_path = str(
                segment["ocr_path"]
                or generation_segment_path(
                    comic_id,
                    chapter_id,
                    generation_id,
                    "ocr",
                    page_index,
                    segment_index,
                    "json",
                )
            )
            blocks_path = str(
                segment["blocks_path"]
                or generation_segment_path(
                    comic_id,
                    chapter_id,
                    generation_id,
                    "blocks",
                    page_index,
                    segment_index,
                    "json",
                )
            )
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
            if ocr_media is not None and blocks_media is not None:
                if not self.repository.mark_segment_ocr_ready(
                    generation_id,
                    page_index,
                    segment_index,
                    ocr_path=ocr_path,
                    blocks_path=blocks_path,
                ):
                    raise RuntimeError("translation segment OCR claim was lost")
                return {
                    "cached": True,
                    "output_bytes": len(ocr_media.content) + len(blocks_media.content),
                }

            input_media = await self._load_ocr_input(
                generation_id,
                comic_id,
                chapter_id,
                page_index,
                segment,
            )
            image, _normalized = await asyncio.to_thread(sanitize_image, input_media.content)
            if ocr_media is not None:
                payload = json.loads(ocr_media.content)
                ocr_output = await self._parse_cached_ocr(pipeline, image, payload)
                ocr_bytes = len(ocr_media.content)
            else:
                persisted_job_id = str(segment["ocr_job_id"]) if segment["ocr_job_id"] else None
                try:
                    ocr_output = await self._run_ocr(
                        pipeline,
                        input_media.content,
                        job_id=persisted_job_id,
                        on_job_submitted=lambda job_id: self.repository.set_segment_job_id(
                            generation_id,
                            page_index,
                            segment_index,
                            job_id,
                        ),
                    )
                except OCRJobNotFoundError:
                    if persisted_job_id is None:
                        raise
                    self.repository.set_segment_job_id(
                        generation_id,
                        page_index,
                        segment_index,
                        None,
                    )
                    ocr_output = await self._run_ocr(
                        pipeline,
                        input_media.content,
                        job_id=None,
                        on_job_submitted=lambda job_id: self.repository.set_segment_job_id(
                            generation_id,
                            page_index,
                            segment_index,
                            job_id,
                        ),
                    )
                ocr_bytes = self._put_json(
                    bundle_key,
                    comic_id,
                    chapter_id,
                    ocr_path,
                    "ocr",
                    ocr_output.payload,
                )
            blocks = self._owned_blocks(ocr_output.blocks, segment)
            blocks_bytes = self._put_json(
                bundle_key,
                comic_id,
                chapter_id,
                blocks_path,
                "blocks",
                [block.as_dict() for block in blocks],
            )
            if not self.repository.mark_segment_ocr_ready(
                generation_id,
                page_index,
                segment_index,
                ocr_path=ocr_path,
                blocks_path=blocks_path,
            ):
                raise RuntimeError("translation segment OCR claim was lost")
            return {
                "ocr_cached": ocr_media is not None,
                "input_bytes": len(input_media.content),
                "output_bytes": ocr_bytes + blocks_bytes,
                "blocks": len(blocks),
            }
        except asyncio.CancelledError:
            self.repository.reset_segment_ocr(generation_id, page_index, segment_index)
            raise

    async def complete_after_ocr(
        self,
        generation_id: str,
        comic_id: str,
        chapter_id: str,
        page_index: int,
        segment_index: int,
        pipeline: ImageTranslationPipeline,
    ) -> None:
        segment = self.repository.segment(generation_id, page_index, segment_index)
        global_index = int(segment["global_index"]) if segment is not None else None
        with task_log_context(
            generation_id=generation_id,
            comic=comic_id,
            chapter=chapter_id,
            page_index=page_index,
            segment_index=segment_index,
            global_index=global_index,
        ):
            await self._complete_after_ocr_in_context(
                generation_id,
                comic_id,
                chapter_id,
                page_index,
                segment_index,
                pipeline,
            )

    async def _complete_after_ocr_in_context(
        self,
        generation_id: str,
        comic_id: str,
        chapter_id: str,
        page_index: int,
        segment_index: int,
        pipeline: ImageTranslationPipeline,
    ) -> None:
        segment = self.repository.segment(generation_id, page_index, segment_index)
        if segment is None:
            raise ValueError("translation segment does not exist")
        if not segment["ocr_path"] or not segment["blocks_path"]:
            raise RuntimeError("translation segment OCR checkpoint is incomplete")
        blocks_media = self.cache.read_bytes(str(segment["blocks_path"]), verify_image=False)
        log_event(
            "task",
            "cache_hit" if blocks_media is not None else "cache_miss",
            artifact="blocks",
        )
        if blocks_media is None:
            raise FileNotFoundError("translation segment OCR blocks cache is missing")
        blocks = [TextBlock.from_dict(value) for value in json.loads(blocks_media.content)]
        input_media = await self._load_ocr_input(
            generation_id,
            comic_id,
            chapter_id,
            page_index,
            segment,
        )
        image, _normalized = await asyncio.to_thread(sanitize_image, input_media.content)
        bundle_key = chapter_bundle_key(comic_id, chapter_id)
        self.repository.set_segment_stage(
            generation_id,
            page_index,
            segment_index,
            "translating",
        )
        with logged_stage(
            "translation",
            blocks=len(blocks),
            input_bytes=len(blocks_media.content),
        ) as translation_summary:
            segment = self.repository.segment(generation_id, page_index, segment_index)
            assert segment is not None
            translations_path = str(
                segment["translations_path"]
                or generation_segment_path(
                    comic_id,
                    chapter_id,
                    generation_id,
                    "translations",
                    page_index,
                    segment_index,
                    "json",
                )
            )
            translations_media = self.cache.read_bytes(translations_path, verify_image=False)
            log_event(
                "task",
                "cache_hit" if translations_media is not None else "cache_miss",
                artifact="translations",
            )
            if translations_media is not None:
                translated_blocks = [
                    TextBlock.from_dict(value) for value in json.loads(translations_media.content)
                ]
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
        self.repository.set_segment_stage(
            generation_id,
            page_index,
            segment_index,
            "rendering",
            paths={"translations_path": translations_path},
        )

        translated_path = generation_segment_path(
            comic_id,
            chapter_id,
            generation_id,
            "translated-segments",
            page_index,
            segment_index,
            "png",
        )
        translated_media = self.cache.read_bytes(
            translated_path,
            media_type="image/png",
            verify_image=True,
        )
        log_event(
            "task",
            "cache_hit" if translated_media is not None else "cache_miss",
            artifact="translated_image",
        )
        render_cached = translated_media is not None
        with logged_stage(
            "render",
            blocks=len(translated_blocks),
            input_bytes=len(input_media.content),
        ) as render_summary:
            if translated_media is None:
                segment = self.repository.segment(generation_id, page_index, segment_index)
                assert segment is not None
                display_image = image.crop(
                    (
                        0,
                        int(segment["display_top"]) - int(segment["ocr_top"]),
                        image.width,
                        int(segment["display_bottom"]) - int(segment["ocr_top"]),
                    )
                )
                render_output = await pipeline.render(display_image, translated_blocks)
                translated_media = self.cache.put_bytes(
                    bundle_key=bundle_key,
                    bundle_kind="chapter",
                    comic_id=comic_id,
                    chapter_id=chapter_id,
                    relative_path=translated_path,
                    entry_kind="translated_segment",
                    content=render_output.translated_bytes,
                    media_type="image/png",
                    verify_image=True,
                )
            self.repository.complete_segment(
                generation_id,
                comic_id,
                chapter_id,
                page_index,
                segment_index,
                translated_path=translated_path,
                translated_version=translated_media.etag,
            )
            render_summary.update(
                cached=render_cached,
                output_bytes=len(translated_media.content),
            )

    async def publish_page_if_complete(
        self,
        generation_id: str,
        comic_id: str,
        chapter_id: str,
        page_index: int,
    ) -> bool:
        if self.repository.finalize_page_from_segments(generation_id, page_index) != "ready":
            return False
        return self.repository.publish_completed_segment_page(
            generation_id,
            comic_id,
            chapter_id,
            page_index,
        )

    async def _load_ocr_input(
        self,
        generation_id: str,
        comic_id: str,
        chapter_id: str,
        page_index: int,
        segment,
    ):
        input_path = str(segment["ocr_input_path"])
        input_media = self.cache.read_bytes(
            input_path,
            media_type="image/png",
            protect=True,
            verify_image=True,
        )
        log_event(
            "task",
            "cache_hit" if input_media is not None else "cache_miss",
            artifact="ocr_input",
        )
        if input_media is not None:
            return input_media

        page = self.repository.page(generation_id, page_index)
        if page is None or not page["original_path"]:
            raise FileNotFoundError("source image cache is missing")
        original = self.cache.read_bytes(
            str(page["original_path"]),
            protect=True,
            verify_image=True,
        )
        log_event(
            "task",
            "cache_hit" if original is not None else "cache_miss",
            artifact="original_image",
        )
        if original is None:
            source_url = str(page["source_url"] or "")
            if not source_url:
                raise FileNotFoundError("source image cache is missing")
            source_content, _media_type = await self.source.fetch_media(source_url)
            source_image, normalized = await asyncio.to_thread(sanitize_image, source_content)
            normalized_checksum = hashlib.sha256(normalized).hexdigest()
            expected_checksum = str(page["original_checksum"] or "")
            expected_size = (int(segment["source_width"]), int(segment["source_height"]))
            if (
                expected_checksum and normalized_checksum != expected_checksum
            ) or source_image.size != expected_size:
                raise ValueError("source image changed after the segment plan was frozen")
            original = self.cache.put_bytes(
                bundle_key=chapter_bundle_key(comic_id, chapter_id),
                bundle_kind="chapter",
                comic_id=comic_id,
                chapter_id=chapter_id,
                relative_path=str(page["original_path"]),
                entry_kind="original",
                content=normalized,
                media_type="image/png",
                protect=True,
                verify_image=True,
            )
            self.repository.save_prepared_page(
                generation_id,
                page_index,
                source_url=source_url,
                original_path=str(page["original_path"]),
                original_checksum=normalized_checksum,
                width=source_image.width,
                height=source_image.height,
            )
        image, _normalized = await asyncio.to_thread(sanitize_image, original.content)
        crop = image.crop(
            (
                0,
                int(segment["ocr_top"]),
                image.width,
                int(segment["ocr_bottom"]),
            )
        )
        content = await asyncio.to_thread(image_to_png_bytes, crop)
        return self.cache.put_bytes(
            bundle_key=chapter_bundle_key(comic_id, chapter_id),
            bundle_kind="chapter",
            comic_id=comic_id,
            chapter_id=chapter_id,
            relative_path=input_path,
            entry_kind="ocr_input",
            content=content,
            media_type="image/png",
            protect=True,
            verify_image=True,
        )

    @staticmethod
    async def _run_ocr(
        pipeline: ImageTranslationPipeline,
        content: bytes,
        *,
        job_id: str | None,
        on_job_submitted,
    ) -> OCROutput:
        checkpointed = getattr(pipeline, "run_segment_ocr", None)
        if checkpointed is not None:
            return await checkpointed(
                content,
                job_id=job_id,
                on_job_submitted=on_job_submitted,
            )
        return await pipeline.run_ocr(content)

    @staticmethod
    async def _parse_cached_ocr(
        pipeline: ImageTranslationPipeline,
        image: Image.Image,
        payload: dict[str, Any],
    ) -> OCROutput:
        parser = getattr(pipeline, "parse_segment_ocr", None)
        if parser is not None:
            return parser(image, payload)
        from app.translation.ocr import extract_text_blocks

        return OCROutput(
            image=image,
            sanitized_bytes=b"",
            payload=payload,
            blocks=extract_text_blocks(payload, image_size=image.size),
            segment_count=1,
        )

    @staticmethod
    def _owned_blocks(blocks: list[TextBlock], segment) -> list[TextBlock]:
        display_top = int(segment["display_top"])
        display_bottom = int(segment["display_bottom"])
        ocr_top = int(segment["ocr_top"])
        owned: list[TextBlock] = []
        for block in blocks:
            x1, y1, x2, y2 = block.bbox
            global_center = ((y1 + y2) / 2) + ocr_top
            if not (display_top <= global_center < display_bottom):
                continue
            block.bbox = (
                x1,
                y1 + ocr_top - display_top,
                x2,
                y2 + ocr_top - display_top,
            )
            owned.append(block)
        return owned

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
