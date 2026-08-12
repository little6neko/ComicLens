from __future__ import annotations

import asyncio
from collections.abc import Callable
from dataclasses import dataclass
from typing import Any

from app.cache.keys import (
    chapter_bundle_key,
    generation_segment_path,
    original_path,
)
from app.cache.storage import MediaCache
from app.repositories.translation import TranslationRepository
from app.sources.base import ComicSource
from app.translation.image_renderer import sanitize_image
from app.translation.image_segments import (
    crop_vertical_slice,
    image_to_png_bytes,
    plan_vertical_slices,
)


@dataclass(frozen=True, slots=True)
class PlannedSegment:
    page_index: int
    segment_index: int
    global_index: int
    source_width: int
    source_height: int
    display_top: int
    display_bottom: int
    ocr_top: int
    ocr_bottom: int
    ocr_input_path: str

    def as_repository_value(self) -> dict[str, object]:
        return {
            "page_index": self.page_index,
            "segment_index": self.segment_index,
            "global_index": self.global_index,
            "source_width": self.source_width,
            "source_height": self.source_height,
            "display_top": self.display_top,
            "display_bottom": self.display_bottom,
            "ocr_top": self.ocr_top,
            "ocr_bottom": self.ocr_bottom,
            "ocr_input_path": self.ocr_input_path,
        }


class SegmentPlanner:
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

    async def prepare(
        self,
        generation_id: str,
        comic_id: str,
        chapter_id: str,
        source_pages: dict[int, str],
        semantic: dict[str, Any],
        *,
        should_stop: Callable[[], bool],
    ) -> bool:
        bundle_key = chapter_bundle_key(comic_id, chapter_id)
        plans: list[PlannedSegment] = []
        global_index = 0

        for page_index, source_url in sorted(source_pages.items()):
            page = self.repository.page(generation_id, page_index)
            if page is None:
                raise ValueError("translation page does not exist")
            relative_path = str(
                page["original_path"] or original_path(comic_id, chapter_id, page_index)
            )
            original = self.cache.read_bytes(
                relative_path,
                protect=True,
                verify_image=True,
            )
            if original is None:
                original = await self.cache.get_or_create(
                    bundle_key=bundle_key,
                    bundle_kind="chapter",
                    comic_id=comic_id,
                    chapter_id=chapter_id,
                    relative_path=relative_path,
                    entry_kind="original",
                    loader=lambda url=source_url: self.source.fetch_media(url),
                    protect=True,
                )

            image, normalized = await asyncio.to_thread(sanitize_image, original.content)
            normalized_media = self.cache.put_bytes(
                bundle_key=bundle_key,
                bundle_kind="chapter",
                comic_id=comic_id,
                chapter_id=chapter_id,
                relative_path=relative_path,
                entry_kind="original",
                content=normalized,
                media_type="image/png",
                protect=True,
                verify_image=True,
            )
            source_unchanged = bool(
                page["original_checksum"]
                and str(page["original_checksum"]) == normalized_media.etag
            )
            self.repository.save_prepared_page(
                generation_id,
                page_index,
                source_url=source_url,
                original_path=relative_path,
                original_checksum=normalized_media.etag,
                width=image.width,
                height=image.height,
            )

            ocr_slices = await asyncio.to_thread(
                plan_vertical_slices,
                image,
                int(semantic["ocrSliceHeight"]),
                int(semantic["ocrSliceOverlap"]),
                int(semantic["longImageThreshold"]),
                float(semantic.get("longImageAspectRatio", 2.6)),
            )
            previous_bottom = 0
            for segment_index, image_slice in enumerate(ocr_slices):
                input_path = generation_segment_path(
                    comic_id,
                    chapter_id,
                    generation_id,
                    "ocr-input",
                    page_index,
                    segment_index,
                    "png",
                )
                existing_input = (
                    self.cache.read_bytes(
                        input_path,
                        media_type="image/png",
                        verify_image=True,
                    )
                    if source_unchanged
                    else None
                )
                if existing_input is None:
                    cropped = await asyncio.to_thread(crop_vertical_slice, image, image_slice)
                    content = await asyncio.to_thread(image_to_png_bytes, cropped)
                    self.cache.put_bytes(
                        bundle_key=bundle_key,
                        bundle_kind="chapter",
                        comic_id=comic_id,
                        chapter_id=chapter_id,
                        relative_path=input_path,
                        entry_kind="ocr_input",
                        content=content,
                        media_type="image/png",
                        verify_image=True,
                    )
                plans.append(
                    PlannedSegment(
                        page_index=page_index,
                        segment_index=segment_index,
                        global_index=global_index,
                        source_width=image.width,
                        source_height=image.height,
                        display_top=previous_bottom,
                        display_bottom=image_slice.bottom,
                        ocr_top=image_slice.top,
                        ocr_bottom=image_slice.bottom,
                        ocr_input_path=input_path,
                    )
                )
                previous_bottom = image_slice.bottom
                global_index += 1

            if should_stop():
                return False

        self.repository.commit_segment_plan(
            generation_id,
            [plan.as_repository_value() for plan in plans],
        )
        return True
