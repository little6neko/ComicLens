from __future__ import annotations

import io
from typing import Any

import httpx
import pytest
from PIL import Image, ImageChops, ImageDraw

from app.translation.image_renderer import render_translated_image, sanitize_image
from app.translation.image_segments import (
    dedupe_text_blocks,
    plan_vertical_slices,
    shift_text_blocks,
)
from app.translation.models import TextBlock
from app.translation.ocr import OCRClient, extract_text_blocks
from app.translation.pipeline import ImageTranslationPipeline, PipelineSettings
from app.translation.translator import DeepLXClient


def image_bytes(width: int, height: int) -> bytes:
    image = Image.new("RGB", (width, height), "white")
    draw = ImageDraw.Draw(image)
    draw.rectangle((10, 10, width - 10, min(height - 10, 80)), fill="gray")
    buffer = io.BytesIO()
    image.save(buffer, format="PNG")
    return buffer.getvalue()


class FakeOCR:
    def __init__(self, *, concurrency: int = 1) -> None:
        self.concurrency = concurrency
        self.calls = 0

    async def analyze_image(self, _content: bytes) -> dict[str, Any]:
        self.calls += 1
        return {"result": {"layoutParsingResults": [{"text": "Hello", "bbox": [10, 10, 80, 40]}]}}


class FakeTranslator:
    def __init__(self) -> None:
        self.calls: list[tuple[str, str]] = []

    async def translate(self, text: str, source_lang: str = "EN") -> str:
        self.calls.append((text, source_lang))
        return f"ZH:{text}"


def test_slice_planning_preserves_overlap_and_full_image_boundary() -> None:
    normal = Image.new("RGB", (500, 900), "white")
    long = Image.new("RGB", (500, 4000), "white")

    normal_slices = plan_vertical_slices(normal, 1200, overlap=100)
    long_slices = plan_vertical_slices(
        long,
        1200,
        overlap=100,
        min_height=1000,
        aspect_ratio_threshold=2,
        search_radius=0,
    )

    assert [(part.top, part.bottom) for part in normal_slices] == [(0, 900)]
    assert long_slices[0].top == 0
    assert long_slices[-1].bottom == 4000
    assert all(
        right.top == left.bottom - 100
        for left, right in zip(long_slices, long_slices[1:], strict=False)
    )


def test_shift_and_dedupe_merge_overlapping_ocr_segments() -> None:
    first = TextBlock("Same text", (10, 900, 100, 960), source_path="first")
    duplicate = TextBlock(" same   text ", (10, 20, 100, 80), source_path="second")
    unique = TextBlock("Another", (10, 100, 100, 160), source_path="second")

    shift_text_blocks([duplicate, unique], offset_y=880, segment_index=2)
    result = dedupe_text_blocks([first, duplicate, unique])

    assert [block.text for block in result] == ["Same text", "Another"]
    assert result[1].bbox == (10, 980, 100, 1040)
    assert result[1].source_path == "second|segment=2"


def test_ocr_parser_normalizes_common_coordinate_shapes_and_filters_noise() -> None:
    payload = {
        "result": {
            "layoutParsingResults": [
                {"text": "Normalized", "bbox": [0.1, 0.2, 0.5, 0.4]},
                {
                    "content": "Polygon",
                    "points": [[10, 20], [90, 20], [90, 60], [10, 60]],
                },
                {"text": "https://advert.example", "bbox": [0, 0, 100, 40]},
                {"text": "...", "bbox": [0, 50, 100, 90]},
            ]
        }
    }

    blocks = extract_text_blocks(payload, image_size=(200, 400))

    assert [(block.text, block.bbox) for block in blocks] == [
        ("Polygon", (10, 20, 90, 60)),
        ("Normalized", (20, 80, 100, 160)),
    ]


@pytest.mark.asyncio
async def test_pipeline_processes_long_image_as_one_source_page() -> None:
    ocr = FakeOCR(concurrency=2)
    translator = FakeTranslator()
    pipeline = ImageTranslationPipeline(
        ocr,
        translator,
        PipelineSettings(
            source_language="EN",
            long_image_threshold=1000,
            long_image_aspect_ratio=2,
            ocr_slice_height=700,
            ocr_slice_overlap=100,
            reading_slice_height=800,
        ),
    )

    ocr_output, translation_output, render_output = await pipeline.process(image_bytes(300, 2400))

    assert ocr.calls > 1
    assert ocr_output.segment_count == ocr.calls
    assert len(ocr_output.blocks) == ocr.calls
    assert all(block.translation == "ZH:Hello" for block in translation_output.blocks)
    assert render_output.width == 300
    assert render_output.height == 2400
    assert len(render_output.display_parts) > 1
    with Image.open(io.BytesIO(render_output.translated_bytes)) as translated:
        assert translated.size == (300, 2400)


def test_renderer_sanitizes_and_changes_only_translated_canvas() -> None:
    raw = image_bytes(200, 120)
    image, sanitized = sanitize_image(raw)
    rendered = render_translated_image(
        image,
        [TextBlock("Hello", (10, 10, 120, 70), translation="Translated")],
    )

    assert image.size == rendered.size == (200, 120)
    assert sanitized.startswith(b"\x89PNG")
    assert ImageChops.difference(image, rendered).getbbox() is not None


@pytest.mark.asyncio
async def test_direct_ocr_and_deeplx_clients_preserve_request_contracts() -> None:
    requests: list[httpx.Request] = []

    def handler(request: httpx.Request) -> httpx.Response:
        requests.append(request)
        if request.url.path == "/ocr":
            return httpx.Response(
                200,
                json={"result": {"layoutParsingResults": []}},
                request=request,
            )
        return httpx.Response(200, json={"data": "你好"}, request=request)

    async with httpx.AsyncClient(transport=httpx.MockTransport(handler)) as client:
        ocr = OCRClient(
            client,
            "https://service.example/ocr",
            token="secret",
            auth_mode="bearer",
            mode="direct",
        )
        translator = DeepLXClient(client, "https://service.example/translate")
        ocr_result = await ocr.analyze_image(image_bytes(20, 20))
        translated = await translator.translate("Hello", "EN")

    assert "result" in ocr_result
    assert translated == "你好"
    assert requests[0].headers["authorization"] == "Bearer secret"
    assert b'"fileType":1' in requests[0].content
    assert requests[1].read().decode() == ('{"text":"Hello","source_lang":"EN","target_lang":"ZH"}')


@pytest.mark.asyncio
async def test_job_ocr_does_not_leak_basic_auth_to_external_result() -> None:
    requests: list[httpx.Request] = []

    def handler(request: httpx.Request) -> httpx.Response:
        requests.append(request)
        if request.url.path == "/jobs":
            return httpx.Response(200, json={"data": {"jobId": "job-1"}}, request=request)
        if request.url.path == "/jobs/job-1":
            return httpx.Response(
                200,
                json={
                    "data": {
                        "state": "done",
                        "resultUrl": {"jsonUrl": "https://files.example/result.jsonl"},
                    }
                },
                request=request,
            )
        return httpx.Response(
            200,
            text='{"result":{"layoutParsingResults":[]}}\n',
            request=request,
        )

    async with httpx.AsyncClient(transport=httpx.MockTransport(handler)) as client:
        ocr = OCRClient(
            client,
            "https://ocr.example/jobs",
            auth_mode="basic",
            basic_username="user",
            basic_password="password",
            mode="job",
            job_poll_interval=0.2,
        )
        await ocr.analyze_image(image_bytes(20, 20))

    assert requests[0].headers.get("authorization", "").startswith("Basic ")
    assert requests[1].headers.get("authorization", "").startswith("Basic ")
    assert "authorization" not in requests[2].headers
