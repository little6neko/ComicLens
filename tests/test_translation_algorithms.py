from __future__ import annotations

import io
import json
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
from app.translation.ocr import OCRClient, OCRProtocolError, extract_text_blocks
from app.translation.pipeline import ImageTranslationPipeline, PipelineSettings
from app.translation.translator import (
    DEEPL_FREE_URL,
    DEEPL_MAX_REQUEST_BYTES,
    DEEPL_PRO_URL,
    DeepLAuthenticationError,
    DeepLClient,
    DeepLQuotaExceededError,
    DeepLRateLimitError,
    DeepLXClient,
    TranslationInputTooLargeError,
    TranslationProtocolError,
)


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
        self.calls: list[tuple[list[str], str]] = []

    async def translate_many(self, texts: list[str], source_lang: str = "AUTO") -> list[str]:
        self.calls.append((texts, source_lang))
        return [f"ZH:{text}" for text in texts]


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
async def test_async_ocr_submits_polls_merges_jsonl_and_isolates_token() -> None:
    requests: list[httpx.Request] = []
    poll_count = 0

    def handler(request: httpx.Request) -> httpx.Response:
        nonlocal poll_count
        requests.append(request)
        if request.url.path == "/jobs":
            return httpx.Response(200, json={"data": {"jobId": "job-1"}}, request=request)
        if request.url.path == "/jobs/job-1":
            poll_count += 1
            if poll_count == 1:
                return httpx.Response(200, json={"data": {"state": "running"}}, request=request)
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
            text=(
                '{"result":{"layoutParsingResults":[{"text":"one","bbox":[1,1,2,2]}]}}\n'
                '{"result":{"layoutParsingResults":[{"text":"two","bbox":[2,2,3,3]}]}}\n'
            ),
            request=request,
        )

    async with httpx.AsyncClient(transport=httpx.MockTransport(handler)) as client:
        ocr = OCRClient(
            client,
            "https://ocr.example/jobs",
            token="secret",
            job_model="PaddleOCR-VL-1.6",
            job_poll_interval=0.2,
            request_timeout=7,
        )
        ocr_result = await ocr.analyze_image(image_bytes(20, 20))

    assert len(ocr_result["result"]["layoutParsingResults"]) == 2
    assert len(requests) == 4
    assert requests[0].headers["authorization"] == "Bearer secret"
    assert b'name="model"' in requests[0].content
    assert b"PaddleOCR-VL-1.6" in requests[0].content
    assert b'name="optionalPayload"' in requests[0].content
    assert b'name="file"; filename="image.png"' in requests[0].content
    assert requests[0].extensions["timeout"]["read"] == 7
    assert requests[1].headers["authorization"] == "Bearer secret"
    assert requests[2].headers["authorization"] == "Bearer secret"
    assert "authorization" not in requests[3].headers


@pytest.mark.asyncio
@pytest.mark.parametrize(
    ("state", "payload", "message"),
    [
        ("failed", {"errorMsg": "cloud rejected image"}, "cloud rejected image"),
        ("mystery", {}, "状态无效"),
        ("done", {"resultUrl": {}}, "缺少 jsonUrl"),
    ],
)
async def test_async_ocr_rejects_failed_or_invalid_job_states(
    state: str,
    payload: dict[str, object],
    message: str,
) -> None:
    def handler(request: httpx.Request) -> httpx.Response:
        if request.url.path == "/jobs":
            return httpx.Response(200, json={"data": {"jobId": "job-1"}}, request=request)
        return httpx.Response(
            200,
            json={"data": {"state": state, **payload}},
            request=request,
        )

    async with httpx.AsyncClient(transport=httpx.MockTransport(handler)) as client:
        ocr = OCRClient(client, "https://ocr.example/jobs", token="secret")
        with pytest.raises(ValueError, match=message):
            await ocr.analyze_image(image_bytes(20, 20))


@pytest.mark.asyncio
async def test_async_ocr_times_out_while_job_remains_pending() -> None:
    def handler(request: httpx.Request) -> httpx.Response:
        if request.url.path == "/jobs":
            return httpx.Response(200, json={"data": {"jobId": "job-1"}}, request=request)
        return httpx.Response(200, json={"data": {"state": "pending"}}, request=request)

    async with httpx.AsyncClient(transport=httpx.MockTransport(handler)) as client:
        ocr = OCRClient(
            client,
            "https://ocr.example/jobs",
            token="secret",
            job_poll_interval=0.2,
            job_timeout=0.2,
        )
        with pytest.raises(TimeoutError, match="OCR 异步任务超时"):
            await ocr.analyze_image(image_bytes(20, 20))


@pytest.mark.asyncio
async def test_async_ocr_rejects_invalid_jsonl() -> None:
    def handler(request: httpx.Request) -> httpx.Response:
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
        return httpx.Response(200, text="not-json", request=request)

    async with httpx.AsyncClient(transport=httpx.MockTransport(handler)) as client:
        ocr = OCRClient(client, "https://ocr.example/jobs", token="secret")
        with pytest.raises(OCRProtocolError, match="JSONL"):
            await ocr.analyze_image(image_bytes(20, 20))


@pytest.mark.asyncio
async def test_deepl_selects_free_or_pro_and_maps_languages() -> None:
    requests: list[httpx.Request] = []

    def handler(request: httpx.Request) -> httpx.Response:
        requests.append(request)
        payload = json.loads(request.read())
        count = len(payload["text"])
        return httpx.Response(
            200,
            json={"translations": [{"text": f"translated-{index}"} for index in range(count)]},
            request=request,
        )

    async with httpx.AsyncClient(transport=httpx.MockTransport(handler)) as client:
        free = DeepLClient(client, "free-key:fx")
        pro = DeepLClient(client, "pro-key")
        free_result = await free.translate_many(["source-0", "", "source-1"], "AUTO")
        pro_result = await pro.translate_many(["source-0"], "KO")

    assert free.url == DEEPL_FREE_URL
    assert pro.url == DEEPL_PRO_URL
    assert free_result == ["translated-0", "", "translated-1"]
    assert pro_result == ["translated-0"]
    assert requests[0].headers["authorization"] == "DeepL-Auth-Key free-key:fx"
    assert requests[0].url.host == "api-free.deepl.com"
    assert requests[0].read().decode() == (
        '{"text":["source-0","source-1"],"target_lang":"ZH-HANS"}'
    )
    assert requests[1].url.host == "api.deepl.com"
    assert '"source_lang":"KO"' in requests[1].read().decode()


@pytest.mark.asyncio
async def test_deepl_batches_by_count_and_preserves_result_order() -> None:
    requests: list[httpx.Request] = []

    def handler(request: httpx.Request) -> httpx.Response:
        requests.append(request)
        texts = json.loads(request.read())["text"]
        return httpx.Response(
            200,
            json={"translations": [{"text": f"ZH:{text}"} for text in texts]},
            request=request,
        )

    texts = [f"line-{index}" for index in range(101)]
    async with httpx.AsyncClient(transport=httpx.MockTransport(handler)) as client:
        translations = await DeepLClient(client, "pro-key", concurrency=3).translate_many(
            texts, "EN"
        )

    assert len(requests) == 3
    assert translations == [f"ZH:{text}" for text in texts]
    assert all(len(request.content) < DEEPL_MAX_REQUEST_BYTES for request in requests)


@pytest.mark.asyncio
async def test_deepl_batches_by_serialized_request_size() -> None:
    requests: list[httpx.Request] = []

    def handler(request: httpx.Request) -> httpx.Response:
        requests.append(request)
        texts = json.loads(request.read())["text"]
        return httpx.Response(
            200,
            json={"translations": [{"text": text[:1]} for text in texts]},
            request=request,
        )

    async with httpx.AsyncClient(transport=httpx.MockTransport(handler)) as client:
        translated = await DeepLClient(client, "pro-key").translate_many(
            ["x" * 70_000, "y" * 70_000],
            "EN",
        )

    assert translated == ["x", "y"]
    assert len(requests) == 2
    assert all(len(request.content) < DEEPL_MAX_REQUEST_BYTES for request in requests)


@pytest.mark.asyncio
async def test_deepl_rejects_oversized_text_and_malformed_response() -> None:
    def handler(request: httpx.Request) -> httpx.Response:
        return httpx.Response(200, json={"translations": []}, request=request)

    async with httpx.AsyncClient(transport=httpx.MockTransport(handler)) as client:
        translator = DeepLClient(client, "pro-key")
        with pytest.raises(TranslationInputTooLargeError):
            await translator.translate_many(["x" * DEEPL_MAX_REQUEST_BYTES], "EN")
        with pytest.raises(TranslationProtocolError, match="数量"):
            await translator.translate_many(["hello"], "EN")


@pytest.mark.asyncio
@pytest.mark.parametrize(
    ("status", "error_type"),
    [
        (403, DeepLAuthenticationError),
        (456, DeepLQuotaExceededError),
        (429, DeepLRateLimitError),
    ],
)
async def test_deepl_classifies_service_statuses(
    status: int,
    error_type: type[ValueError],
) -> None:
    def handler(request: httpx.Request) -> httpx.Response:
        return httpx.Response(status, request=request)

    async with httpx.AsyncClient(transport=httpx.MockTransport(handler)) as client:
        translator = DeepLClient(client, "pro-key")
        with pytest.raises(error_type):
            await translator.translate_many(["hello"], "AUTO")


@pytest.mark.asyncio
async def test_deeplx_translates_each_text_and_maps_auto_language() -> None:
    requests: list[httpx.Request] = []

    def handler(request: httpx.Request) -> httpx.Response:
        requests.append(request)
        return httpx.Response(200, json={"data": "你好"}, request=request)

    async with httpx.AsyncClient(transport=httpx.MockTransport(handler)) as client:
        translator = DeepLXClient(client, "https://service.example/translate", timeout=11)
        translated = await translator.translate_many(["Hello", "World"], "AUTO")

    assert translated == ["你好", "你好"]
    assert len(requests) == 2
    assert requests[0].read().decode() == (
        '{"text":"Hello","source_lang":"auto","target_lang":"ZH"}'
    )
    assert requests[0].extensions["timeout"]["read"] == 11
