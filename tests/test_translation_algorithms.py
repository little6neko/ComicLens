from __future__ import annotations

import base64
import io
import json
import logging
from typing import Any
from unittest.mock import AsyncMock

import httpx
import pytest
from PIL import Image, ImageChops, ImageDraw

from app.observability import short_ref
from app.translation.image_renderer import render_translated_image, sanitize_image
from app.translation.image_segments import (
    dedupe_text_blocks,
    plan_vertical_slices,
    shift_text_blocks,
)
from app.translation.models import TextBlock
from app.translation.ocr import (
    OCRClient,
    OCRJobNotFoundError,
    OCRProtocolError,
    extract_text_blocks,
    resolve_ocr_protocol,
)
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


@pytest.mark.parametrize(
    ("mode", "url", "expected"),
    [
        ("auto", "https://ocr.example/api/v2/ocr/jobs", "job"),
        ("auto", "https://ocr.example/api/v2/ocr/jobs/", "job"),
        ("auto", "https://ocr.example/custom/ocr/jobs?region=test", "job"),
        ("auto", "https://ocr.example/layout-parsing", "direct"),
        ("auto", "https://ocr.example/v1", "direct"),
        ("auto", "https://ocr.example/api/v2/ocr/jobs/123", "direct"),
        ("auto", "https://ocr.example/api/v2/ocr/jobs-old", "direct"),
        ("direct", "https://ocr.example/api/v2/ocr/jobs", "direct"),
        ("job", "https://ocr.example/layout-parsing", "job"),
    ],
)
def test_ocr_protocol_resolution(mode: str, url: str, expected: str) -> None:
    assert resolve_ocr_protocol(mode, url) == expected


def test_ocr_protocol_resolution_rejects_unknown_explicit_mode() -> None:
    with pytest.raises(ValueError, match="OCR 模式无效"):
        resolve_ocr_protocol("probe", "https://ocr.example/layout-parsing")


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
async def test_direct_ocr_posts_base64_json_without_auth_or_job_callbacks() -> None:
    requests: list[httpx.Request] = []
    submitted: list[str] = []
    content = image_bytes(20, 20)

    def handler(request: httpx.Request) -> httpx.Response:
        requests.append(request)
        return httpx.Response(
            200,
            json={"result": {"layoutParsingResults": []}},
            request=request,
        )

    async with httpx.AsyncClient(transport=httpx.MockTransport(handler)) as client:
        ocr = OCRClient(
            client,
            "https://ocr.example/layout-parsing",
            token="unused-token",
            auth_mode="none",
            mode="auto",
            request_timeout=11,
        )
        result = await ocr.analyze_image(
            content,
            job_id="stale-job",
            on_job_submitted=submitted.append,
        )

    assert result == {"result": {"layoutParsingResults": []}}
    assert len(requests) == 1
    request = requests[0]
    payload = json.loads(request.read())
    assert request.method == "POST"
    assert request.url.path == "/layout-parsing"
    assert request.headers["content-type"] == "application/json"
    assert "authorization" not in request.headers
    assert base64.b64decode(payload.pop("file")) == content
    assert payload == {
        "fileType": 1,
        "useDocOrientationClassify": False,
        "useDocUnwarping": False,
        "useChartRecognition": False,
        "visualize": False,
    }
    assert request.extensions["timeout"]["read"] == 11
    assert submitted == []


@pytest.mark.asyncio
async def test_direct_ocr_logs_safe_request_response_and_completion_summaries(
    caplog: pytest.LogCaptureFixture,
) -> None:
    token = "private-ocr-token-canary"
    content = image_bytes(20, 20)
    encoded_image = base64.b64encode(content).decode("ascii")

    def handler(request: httpx.Request) -> httpx.Response:
        return httpx.Response(
            200,
            json={"result": {"layoutParsingResults": [{"type": "text"}]}},
            request=request,
        )

    async with httpx.AsyncClient(transport=httpx.MockTransport(handler)) as client:
        ocr = OCRClient(
            client,
            "https://ocr.example/layout-parsing?api_key=private-query-canary",
            token=token,
            auth_mode="bearer",
            mode="direct",
        )
        with caplog.at_level(logging.INFO, logger="comiclens.events"):
            await ocr.analyze_image(content)

    messages = [
        record.getMessage() for record in caplog.records if record.getMessage().startswith("ocr ")
    ]
    request = next(message for message in messages if "event=request" in message)
    response = next(message for message in messages if "event=response" in message)
    completed = next(message for message in messages if "event=completed" in message)
    assert request.startswith("ocr event=request operation=analyze")
    assert "protocol=direct" in request
    assert "auth=bearer" in request
    assert f"image_bytes={len(content)}" in request
    assert "payload_bytes=" in request
    assert "endpoint=https://ocr.example/layout-parsing" in request
    assert "attempt=1" in request
    assert response.startswith("ocr event=response operation=analyze")
    assert "status=200" in response
    assert "duration_ms=" in response
    assert "response_bytes=" in response
    assert "content_type=application/json" in response
    assert "layout_results=1" in completed
    assert all(token not in message for message in messages)
    assert all("private-query-canary" not in message for message in messages)
    assert all(encoded_image not in message for message in messages)


@pytest.mark.asyncio
@pytest.mark.parametrize(
    ("body", "message"),
    [
        (b"not-json", "不是有效 JSON"),
        (b"[]", "不是对象"),
        (b"{}", "缺少 result"),
    ],
)
async def test_direct_ocr_rejects_malformed_response(body: bytes, message: str) -> None:
    def handler(request: httpx.Request) -> httpx.Response:
        return httpx.Response(200, content=body, request=request)

    async with httpx.AsyncClient(transport=httpx.MockTransport(handler)) as client:
        ocr = OCRClient(
            client,
            "https://ocr.example/layout-parsing",
            auth_mode="none",
            mode="direct",
        )
        with pytest.raises(OCRProtocolError, match=message):
            await ocr.analyze_image(image_bytes(20, 20))


@pytest.mark.asyncio
@pytest.mark.parametrize(
    ("auth_mode", "kwargs", "expected_authorization"),
    [
        ("none", {"token": "ignored"}, None),
        ("bearer", {"token": "test-token"}, "Bearer test-token"),
        (
            "basic",
            {"basic_username": "test-user", "basic_password": "test-password"},
            "Basic dGVzdC11c2VyOnRlc3QtcGFzc3dvcmQ=",
        ),
    ],
)
async def test_direct_ocr_supports_configured_auth_modes(
    auth_mode: str,
    kwargs: dict[str, str],
    expected_authorization: str | None,
) -> None:
    requests: list[httpx.Request] = []

    def handler(request: httpx.Request) -> httpx.Response:
        requests.append(request)
        return httpx.Response(
            200,
            json={"result": {"layoutParsingResults": []}},
            request=request,
        )

    async with httpx.AsyncClient(transport=httpx.MockTransport(handler)) as client:
        ocr = OCRClient(
            client,
            "https://ocr.example/layout-parsing",
            auth_mode=auth_mode,
            mode="direct",
            **kwargs,
        )
        await ocr.analyze_image(image_bytes(20, 20))

    assert requests[0].headers.get("authorization") == expected_authorization


@pytest.mark.asyncio
@pytest.mark.parametrize(
    ("kwargs", "message"),
    [
        ({"auth_mode": "bearer"}, "Token"),
        ({"auth_mode": "basic", "basic_username": "user"}, "用户名或密码"),
        ({"auth_mode": "invalid"}, "认证模式无效"),
    ],
)
async def test_ocr_rejects_missing_or_invalid_auth_configuration(
    kwargs: dict[str, str],
    message: str,
) -> None:
    async with httpx.AsyncClient(transport=httpx.MockTransport(lambda request: None)) as client:
        ocr = OCRClient(
            client,
            "https://ocr.example/layout-parsing",
            mode="direct",
            **kwargs,
        )
        with pytest.raises(ValueError, match=message):
            await ocr.analyze_image(image_bytes(20, 20))


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
            mode="job",
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
    assert b'"useDocOrientationClassify": false' in requests[0].content
    assert b'"useDocUnwarping": false' in requests[0].content
    assert b'"useChartRecognition": false' in requests[0].content
    assert b"useOcrForImageBlock" not in requests[0].content
    assert b'name="file"; filename="image.png"' in requests[0].content
    assert requests[0].extensions["timeout"]["read"] == 7
    assert requests[1].headers["authorization"] == "Bearer secret"
    assert requests[2].headers["authorization"] == "Bearer secret"
    assert "authorization" not in requests[3].headers


@pytest.mark.asyncio
async def test_async_ocr_logs_each_poll_at_debug_and_only_state_changes_at_info(
    caplog: pytest.LogCaptureFixture,
) -> None:
    job_id = "private-job-id-canary"
    token = "private-job-token-canary"
    poll_states = iter(("running", "running", "done"))

    def handler(request: httpx.Request) -> httpx.Response:
        if request.url.path == "/ocr/jobs":
            return httpx.Response(200, json={"data": {"jobId": job_id}}, request=request)
        if request.url.path.endswith(job_id):
            state = next(poll_states)
            payload: dict[str, object] = {"state": state}
            if state == "done":
                payload["resultUrl"] = {
                    "jsonUrl": (
                        "https://result-user:result-password@files.example/"
                        f"private/{job_id}/result.jsonl?signature=private-signature"
                    )
                }
            return httpx.Response(200, json={"data": payload}, request=request)
        return httpx.Response(
            200,
            text='{"result":{"layoutParsingResults":[]}}\n',
            request=request,
        )

    async with httpx.AsyncClient(transport=httpx.MockTransport(handler)) as client:
        ocr = OCRClient(
            client,
            "https://ocr.example/ocr/jobs",
            token=token,
            mode="job",
            job_poll_interval=0.2,
        )
        with caplog.at_level(logging.DEBUG, logger="comiclens.events"):
            await ocr.analyze_image(image_bytes(20, 20))

    records = [record for record in caplog.records if record.getMessage().startswith("ocr ")]
    poll_requests = [
        record
        for record in records
        if "event=request" in record.getMessage() and "operation=poll" in record.getMessage()
    ]
    poll_responses = [
        record
        for record in records
        if "event=response" in record.getMessage() and "operation=poll" in record.getMessage()
    ]
    states = [record for record in records if "event=state" in record.getMessage()]
    assert len(poll_requests) == 3
    assert len(poll_responses) == 3
    assert all(record.levelno == logging.DEBUG for record in poll_requests)
    assert all(record.levelno == logging.DEBUG for record in poll_responses)
    assert [
        "state=running" in states[0].getMessage(),
        "state=done" in states[1].getMessage(),
    ] == [True, True]
    assert len(states) == 2
    assert all(record.levelno == logging.INFO for record in states)
    messages = [record.getMessage() for record in records]
    assert any(f"job_ref={short_ref(job_id)}" in message for message in messages)
    download_request = next(
        message
        for message in messages
        if "event=request" in message and "operation=download_result" in message
    )
    assert "endpoint=https://files.example" in download_request
    assert "/private/" not in download_request
    assert all(job_id not in message for message in messages)
    assert all(token not in message for message in messages)
    assert all("result-user" not in message for message in messages)
    assert all("result-password" not in message for message in messages)
    assert all("private-signature" not in message for message in messages)


@pytest.mark.asyncio
async def test_ocr_retries_and_logs_redacted_json_failures(
    monkeypatch: pytest.MonkeyPatch,
    caplog: pytest.LogCaptureFixture,
) -> None:
    token = "private-retry-token-canary"
    source_text = "private-ocr-source-canary"

    def handler(request: httpx.Request) -> httpx.Response:
        return httpx.Response(
            503,
            json={
                "code": "OVERLOADED",
                "apiKey": token,
                "file": base64.b64encode(source_text.encode()).decode(),
                "message": (
                    "retry https://url-user:url-password@ocr.example/failure"
                    "?token=private-query-token"
                ),
            },
            request=request,
        )

    monkeypatch.setattr("app.translation.ocr.asyncio.sleep", AsyncMock())
    async with httpx.AsyncClient(transport=httpx.MockTransport(handler)) as client:
        ocr = OCRClient(
            client,
            "https://ocr.example/layout-parsing",
            token=token,
            mode="direct",
        )
        with (
            caplog.at_level(logging.DEBUG, logger="comiclens.events"),
            pytest.raises(httpx.HTTPStatusError),
        ):
            await ocr.analyze_image(source_text.encode())

    records = [record for record in caplog.records if record.getMessage().startswith("ocr ")]
    messages = [record.getMessage() for record in records]
    assert sum("event=request" in message for message in messages) == 3
    assert sum("event=response" in message for message in messages) == 3
    assert sum("event=retry" in message for message in messages) == 2
    assert sum("event=failed" in message for message in messages) == 1
    excerpts = [message for message in messages if "event=error_detail" in message]
    assert len(excerpts) == 3
    assert all("OVERLOADED" in message for message in excerpts)
    assert all("<redacted>" in message for message in excerpts)
    assert all(token not in message for message in messages)
    assert all(source_text not in message for message in messages)
    assert all("url-user" not in message for message in messages)
    assert all("url-password" not in message for message in messages)
    assert all("private-query-token" not in message for message in messages)


@pytest.mark.asyncio
@pytest.mark.parametrize(
    ("result_url", "expected_result_auth"),
    [
        ("https://ocr.example:443/results/result.jsonl", True),
        ("https://files.example/results/result.jsonl", False),
    ],
)
async def test_async_ocr_sends_basic_to_api_and_only_same_origin_result(
    result_url: str,
    expected_result_auth: bool,
) -> None:
    requests: list[httpx.Request] = []

    def handler(request: httpx.Request) -> httpx.Response:
        requests.append(request)
        if request.url.path == "/ocr/jobs":
            return httpx.Response(200, json={"data": {"jobId": "job-1"}}, request=request)
        if request.url.path == "/ocr/jobs/job-1":
            return httpx.Response(
                200,
                json={
                    "data": {
                        "state": "done",
                        "resultUrl": {"jsonUrl": result_url},
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
            "https://ocr.example/ocr/jobs",
            auth_mode="basic",
            basic_username="test-user",
            basic_password="test-password",
            mode="auto",
        )
        await ocr.analyze_image(image_bytes(20, 20))

    expected_header = "Basic dGVzdC11c2VyOnRlc3QtcGFzc3dvcmQ="
    assert requests[0].headers["authorization"] == expected_header
    assert requests[1].headers["authorization"] == expected_header
    assert (requests[2].headers.get("authorization") == expected_header) is expected_result_auth


@pytest.mark.asyncio
async def test_async_ocr_resumes_persisted_job_without_resubmitting() -> None:
    requests: list[httpx.Request] = []
    submitted: list[str] = []

    def handler(request: httpx.Request) -> httpx.Response:
        requests.append(request)
        if request.url.path == "/jobs/persisted-job":
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
        ocr = OCRClient(client, "https://ocr.example/jobs", token="secret", mode="job")
        result = await ocr.analyze_image(
            image_bytes(20, 20),
            job_id="persisted-job",
            on_job_submitted=submitted.append,
        )

    assert result == {"result": {"layoutParsingResults": []}}
    assert [request.method for request in requests] == ["GET", "GET"]
    assert submitted == []


@pytest.mark.asyncio
async def test_async_ocr_reports_expired_persisted_job() -> None:
    def handler(request: httpx.Request) -> httpx.Response:
        return httpx.Response(404, text="gone", request=request)

    async with httpx.AsyncClient(transport=httpx.MockTransport(handler)) as client:
        ocr = OCRClient(client, "https://ocr.example/jobs", token="secret", mode="job")
        with pytest.raises(OCRJobNotFoundError, match="已失效"):
            await ocr.analyze_image(image_bytes(20, 20), job_id="expired-job")


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
        ocr = OCRClient(client, "https://ocr.example/jobs", token="secret", mode="job")
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
            mode="job",
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
        ocr = OCRClient(client, "https://ocr.example/jobs", token="secret", mode="job")
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
        '{"text":["source-0","source-1"],"target_lang":"ZH-HANS",'
        '"model_type":"quality_optimized"}'
    )
    assert requests[1].url.host == "api.deepl.com"
    assert json.loads(requests[1].read()) == {
        "text": ["source-0"],
        "target_lang": "ZH-HANS",
        "model_type": "quality_optimized",
        "source_lang": "KO",
    }


@pytest.mark.asyncio
async def test_deepl_logs_batch_summaries_without_key_source_or_translation(
    caplog: pytest.LogCaptureFixture,
) -> None:
    api_key = "private-deepl-key-canary"
    source_texts = ["private source alpha", "private source beta"]
    translations = ["private target alpha", "private target beta"]

    def handler(request: httpx.Request) -> httpx.Response:
        return httpx.Response(
            200,
            json={"translations": [{"text": text} for text in translations]},
            request=request,
        )

    async with httpx.AsyncClient(transport=httpx.MockTransport(handler)) as client:
        translator = DeepLClient(client, api_key)
        with caplog.at_level(logging.INFO, logger="comiclens.events"):
            result = await translator.translate_many(source_texts, "EN")

    assert result == translations
    messages = [
        record.getMessage() for record in caplog.records if record.getMessage().startswith("deepl ")
    ]
    request = next(message for message in messages if "event=request" in message)
    response = next(message for message in messages if "event=response" in message)
    completed = next(message for message in messages if "event=completed" in message)
    assert request.startswith("deepl event=request operation=translate_batch")
    assert "method=POST" in request
    assert "endpoint=https://api.deepl.com/v2/translate" in request
    assert "auth=api_key" in request
    assert "text_count=2" in request
    assert f"total_chars={sum(map(len, source_texts))}" in request
    assert "payload_bytes=" in request
    assert "source_lang=EN" in request
    assert "target_lang=ZH-HANS" in request
    assert response.startswith("deepl event=response operation=translate_batch")
    assert "status=200" in response
    assert "duration_ms=" in response
    assert "response_bytes=" in response
    assert "content_type=application/json" in response
    assert "success_count=2" in completed
    assert all(api_key not in message for message in messages)
    assert all(
        text not in message for message in messages for text in [*source_texts, *translations]
    )


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


@pytest.mark.asyncio
async def test_deeplx_logs_each_nonempty_text_without_url_secrets_or_content(
    caplog: pytest.LogCaptureFixture,
) -> None:
    source_texts = ["private deeplx source", "", "second private source"]
    target_text = "private deeplx target"

    def handler(request: httpx.Request) -> httpx.Response:
        return httpx.Response(200, json={"data": target_text}, request=request)

    async with httpx.AsyncClient(transport=httpx.MockTransport(handler)) as client:
        translator = DeepLXClient(
            client,
            "https://url-user:url-password@service.example/translate?token=private-query-token",
        )
        with caplog.at_level(logging.INFO, logger="comiclens.events"):
            result = await translator.translate_many(source_texts, "AUTO")

    assert result == [target_text, "", target_text]
    messages = [
        record.getMessage()
        for record in caplog.records
        if record.getMessage().startswith("deeplx ")
    ]
    requests = [message for message in messages if "event=request" in message]
    responses = [message for message in messages if "event=response" in message]
    completed = [message for message in messages if "event=completed" in message]
    assert len(requests) == 2
    assert len(responses) == 2
    assert len(completed) == 2
    assert all(
        message.startswith("deeplx event=request operation=translate_text") for message in requests
    )
    assert all("endpoint=https://service.example/translate" in message for message in requests)
    assert sorted(
        int(message.split("source_chars=", 1)[1].split(" ", 1)[0]) for message in requests
    ) == sorted(len(text) for text in source_texts if text)
    assert all("payload_bytes=" in message for message in requests)
    assert all("source_lang=auto" in message for message in requests)
    assert all("target_lang=ZH" in message for message in requests)
    assert all("success_count=1" in message for message in completed)
    assert all(
        secret not in message
        for message in messages
        for secret in [
            *source_texts,
            target_text,
            "url-user",
            "url-password",
            "private-query-token",
        ]
        if secret
    )


@pytest.mark.asyncio
async def test_translation_failure_logs_retry_and_redacted_json_details(
    monkeypatch: pytest.MonkeyPatch,
    caplog: pytest.LogCaptureFixture,
) -> None:
    api_key = "private-translation-key-canary"
    source_text = "private translation source canary"

    def deepl_handler(request: httpx.Request) -> httpx.Response:
        return httpx.Response(
            503,
            json={
                "code": "TEMPORARY_UNAVAILABLE",
                "authorizationKey": api_key,
                "text": source_text,
            },
            request=request,
        )

    def deeplx_handler(request: httpx.Request) -> httpx.Response:
        return httpx.Response(
            400,
            json={
                "code": "INVALID_REQUEST",
                "text": source_text,
                "message": (
                    "see https://url-user:url-password@service.example/error"
                    "?token=private-query-token"
                ),
            },
            request=request,
        )

    monkeypatch.setattr("app.translation.translator.asyncio.sleep", AsyncMock())
    with caplog.at_level(logging.DEBUG, logger="comiclens.events"):
        async with httpx.AsyncClient(transport=httpx.MockTransport(deepl_handler)) as deepl_client:
            with pytest.raises(httpx.HTTPStatusError):
                await DeepLClient(deepl_client, api_key).translate_many([source_text])
        async with httpx.AsyncClient(
            transport=httpx.MockTransport(deeplx_handler)
        ) as deeplx_client:
            with pytest.raises(httpx.HTTPStatusError):
                await DeepLXClient(
                    deeplx_client,
                    "https://service.example/translate",
                ).translate_many([source_text])

    records = [
        record for record in caplog.records if record.getMessage().startswith(("deepl ", "deeplx "))
    ]
    messages = [record.getMessage() for record in records]
    deepl_messages = [message for message in messages if message.startswith("deepl ")]
    deeplx_messages = [message for message in messages if message.startswith("deeplx ")]
    assert sum("event=request" in message for message in deepl_messages) == 3
    assert sum("event=response" in message for message in deepl_messages) == 3
    assert sum("event=retry" in message for message in deepl_messages) == 2
    assert sum("event=failed" in message for message in deepl_messages) == 1
    assert sum("event=error_detail" in message for message in deepl_messages) == 3
    assert sum("event=request" in message for message in deeplx_messages) == 1
    assert sum("event=response" in message for message in deeplx_messages) == 1
    assert sum("event=failed" in message for message in deeplx_messages) == 1
    assert sum("event=error_detail" in message for message in deeplx_messages) == 1
    assert any("TEMPORARY_UNAVAILABLE" in message for message in deepl_messages)
    assert any("INVALID_REQUEST" in message for message in deeplx_messages)
    assert all(api_key not in message for message in messages)
    assert all(source_text not in message for message in messages)
    assert all("url-user" not in message for message in messages)
    assert all("url-password" not in message for message in messages)
    assert all("private-query-token" not in message for message in messages)
