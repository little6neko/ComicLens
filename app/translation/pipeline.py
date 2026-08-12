from __future__ import annotations

import asyncio
from dataclasses import dataclass
from typing import Any, Protocol

from PIL import Image

from app.translation.image_renderer import (
    image_to_png_bytes as rendered_png_bytes,
)
from app.translation.image_renderer import render_translated_image, sanitize_image
from app.translation.image_segments import (
    crop_vertical_slice,
    dedupe_text_blocks,
    image_to_png_bytes,
    plan_vertical_slices,
    shift_text_blocks,
)
from app.translation.models import TextBlock
from app.translation.ocr import extract_text_blocks


class OCRBackend(Protocol):
    concurrency: int

    async def analyze_image(self, image_bytes: bytes) -> dict[str, Any]: ...


class TranslatorBackend(Protocol):
    async def translate(self, text: str, source_lang: str = "EN") -> str | None: ...


@dataclass(frozen=True, slots=True)
class PipelineSettings:
    source_language: str = "EN"
    long_image_threshold: int = 2800
    long_image_aspect_ratio: float = 2.6
    ocr_slice_height: int = 2200
    ocr_slice_overlap: int = 180
    reading_slice_height: int = 1800
    font_path: str = ""


@dataclass(slots=True)
class OCROutput:
    image: Image.Image
    sanitized_bytes: bytes
    payload: dict[str, Any]
    blocks: list[TextBlock]
    segment_count: int


@dataclass(slots=True)
class TranslationOutput:
    blocks: list[TextBlock]
    translated_count: int


@dataclass(slots=True)
class RenderOutput:
    translated_bytes: bytes
    width: int
    height: int
    display_parts: list[bytes]


class ImageTranslationPipeline:
    def __init__(
        self,
        ocr: OCRBackend,
        translator: TranslatorBackend,
        settings: PipelineSettings,
    ) -> None:
        self.ocr = ocr
        self.translator = translator
        self.settings = settings

    async def run_ocr(self, original_bytes: bytes) -> OCROutput:
        image, sanitized_bytes = await asyncio.to_thread(sanitize_image, original_bytes)
        slices = await asyncio.to_thread(
            plan_vertical_slices,
            image,
            self.settings.ocr_slice_height,
            self.settings.ocr_slice_overlap,
            self.settings.long_image_threshold,
            self.settings.long_image_aspect_ratio,
        )

        if len(slices) == 1:
            payload = await self.ocr.analyze_image(sanitized_bytes)
            blocks = extract_text_blocks(payload, image_size=image.size)
            return OCROutput(
                image=image,
                sanitized_bytes=sanitized_bytes,
                payload=payload,
                blocks=blocks,
                segment_count=1,
            )

        async def analyze_segment(image_slice):
            cropped = await asyncio.to_thread(crop_vertical_slice, image, image_slice)
            content = await asyncio.to_thread(image_to_png_bytes, cropped)
            payload = await self.ocr.analyze_image(content)
            blocks = extract_text_blocks(payload, image_size=cropped.size)
            shift_text_blocks(blocks, image_slice.top, image_slice.index)
            return image_slice, payload, blocks

        if self.ocr.concurrency == 1:
            segment_results = []
            for image_slice in slices:
                segment_results.append(await analyze_segment(image_slice))
        else:
            segment_results = await asyncio.gather(
                *(analyze_segment(image_slice) for image_slice in slices)
            )

        merged_blocks: list[TextBlock] = []
        segments: list[dict[str, Any]] = []
        for image_slice, payload, blocks in segment_results:
            merged_blocks.extend(blocks)
            segments.append(
                {
                    **image_slice.as_dict(),
                    "ocrPayload": payload,
                }
            )
        combined_payload = {
            "mode": "segmented-ocr",
            "imageSize": {"width": image.width, "height": image.height},
            "segmentCount": len(slices),
            "segments": segments,
        }
        return OCROutput(
            image=image,
            sanitized_bytes=sanitized_bytes,
            payload=combined_payload,
            blocks=dedupe_text_blocks(merged_blocks),
            segment_count=len(slices),
        )

    async def translate_blocks(self, blocks: list[TextBlock]) -> TranslationOutput:
        async def translate_one(block: TextBlock) -> str | None:
            return await self.translator.translate(
                block.text, source_lang=self.settings.source_language
            )

        translations = await asyncio.gather(*(translate_one(block) for block in blocks))
        translated_count = 0
        for block, translated in zip(blocks, translations, strict=True):
            if translated:
                block.translation = translated
                translated_count += 1
        return TranslationOutput(
            blocks=blocks,
            translated_count=translated_count,
        )

    async def render(self, image: Image.Image, blocks: list[TextBlock]) -> RenderOutput:
        rendered = await asyncio.to_thread(
            render_translated_image,
            image,
            blocks,
            self.settings.font_path,
        )
        translated_bytes = await asyncio.to_thread(rendered_png_bytes, rendered)
        display_parts: list[bytes] = []
        display_slices = await asyncio.to_thread(
            plan_vertical_slices,
            rendered,
            self.settings.reading_slice_height,
            0,
            self.settings.long_image_threshold,
            self.settings.long_image_aspect_ratio,
        )
        if len(display_slices) > 1:
            for display_slice in display_slices:
                cropped = await asyncio.to_thread(crop_vertical_slice, rendered, display_slice)
                display_parts.append(await asyncio.to_thread(rendered_png_bytes, cropped))
        return RenderOutput(
            translated_bytes=translated_bytes,
            width=rendered.width,
            height=rendered.height,
            display_parts=display_parts,
        )

    async def process(
        self, original_bytes: bytes
    ) -> tuple[OCROutput, TranslationOutput, RenderOutput]:
        ocr_output = await self.run_ocr(original_bytes)
        translation_output = await self.translate_blocks(ocr_output.blocks)
        render_output = await self.render(ocr_output.image, translation_output.blocks)
        return ocr_output, translation_output, render_output
