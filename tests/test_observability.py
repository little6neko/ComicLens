from __future__ import annotations

import asyncio
import json
import logging

import pytest

from app.observability import (
    LOG_FORMAT,
    current_log_context,
    format_event,
    log_event,
    logged_stage,
    new_request_ref,
    safe_endpoint,
    safe_error_excerpt,
    short_ref,
    task_log_context,
)


def test_event_format_starts_with_service_and_event_and_is_single_line() -> None:
    message = format_event(
        "ocr",
        "request",
        operation="analyze",
        attempt=1,
        cached=False,
        detail="first\nsecond\tline",
    )

    assert message == (
        'ocr event=request operation=analyze attempt=1 cached=false detail="first second line"'
    )
    assert "\n" not in message
    assert "\t" not in message


def test_application_formatter_omits_timestamp_and_logger_name() -> None:
    formatter = logging.Formatter(LOG_FORMAT)
    record = logging.LogRecord(
        name="comiclens.events",
        level=logging.INFO,
        pathname=__file__,
        lineno=1,
        msg="ocr event=response status=200",
        args=(),
        exc_info=None,
    )

    assert formatter.format(record) == "INFO ocr event=response status=200"


def test_log_event_merges_nested_task_context_without_exposing_generation_id(
    caplog: pytest.LogCaptureFixture,
) -> None:
    generation_id = "generation-canary-value"
    with (
        caplog.at_level(logging.INFO, logger="comiclens.events"),
        task_log_context(
            generation_id=generation_id,
            comic="demo-comic",
            chapter="chapter-12",
        ),
        task_log_context(page_index=3, segment_index=2, global_index=89),
    ):
        log_event("task", "stage_start", stage="ocr")

    message = caplog.records[-1].getMessage()
    assert message.startswith("task event=stage_start stage=ocr")
    assert f"generation_ref={short_ref(generation_id)}" in message
    assert "comic=demo-comic" in message
    assert "chapter=chapter-12" in message
    assert "page_index=3" in message
    assert "segment_index=2" in message
    assert "global_index=89" in message
    assert generation_id not in message
    assert current_log_context() == {}


@pytest.mark.asyncio
async def test_task_context_is_isolated_between_concurrent_tasks() -> None:
    async def read_context(generation_id: str) -> dict[str, object]:
        with task_log_context(generation_id=generation_id):
            await asyncio.sleep(0)
            return current_log_context()

    first, second = await asyncio.gather(
        read_context("generation-one"),
        read_context("generation-two"),
    )

    assert first == {"generation_ref": short_ref("generation-one")}
    assert second == {"generation_ref": short_ref("generation-two")}
    assert current_log_context() == {}


def test_safe_endpoint_removes_credentials_query_fragment_and_known_secrets() -> None:
    endpoint = safe_endpoint(
        "https://url-user:url-password@ocr.example:443/private-token/layout"
        "?api_key=query-secret#fragment-secret",
        secrets=("private-token", "url-user", "url-password", "query-secret"),
    )

    assert endpoint == "https://ocr.example/<redacted>/layout"
    assert "url-user" not in endpoint
    assert "url-password" not in endpoint
    assert "api_key" not in endpoint
    assert "query-secret" not in endpoint
    assert "fragment-secret" not in endpoint


def test_safe_endpoint_can_limit_ocr_result_url_to_origin() -> None:
    endpoint = safe_endpoint(
        "https://storage.example/signed/private-result.json?signature=secret",
        origin_only=True,
    )

    assert endpoint == "https://storage.example"


@pytest.mark.parametrize(
    "url",
    ["", "relative/path", "http://[invalid-host", "ftp://files.example/result"],
)
def test_safe_endpoint_does_not_echo_invalid_url(url: str) -> None:
    assert safe_endpoint(url) == "<invalid-url>"


def test_safe_error_excerpt_redacts_json_keys_literals_urls_and_source_text() -> None:
    api_key = "canary-api-key"
    source_text = "private source sentence"
    content = (
        "{"
        '"code":"BAD_REQUEST",'
        '"message":"request failed at https://user:pass@example.com/path?token=value",'
        f'"apiKey":"{api_key}",'
        f'"text":"{source_text}",'
        '"nested":{"result":{"translation":"private target sentence"}}'
        "}"
    ).encode()

    excerpt, truncated = safe_error_excerpt(
        content,
        "application/json; charset=utf-8",
        secrets=(api_key,),
        sensitive_texts=(source_text,),
    )

    assert excerpt is not None
    assert "BAD_REQUEST" in excerpt
    assert "https://example.com/path" in excerpt
    assert excerpt.count("<redacted>") >= 3
    assert api_key not in excerpt
    assert source_text not in excerpt
    assert "private target sentence" not in excerpt
    assert "user:pass" not in excerpt
    assert "token=value" not in excerpt
    assert truncated is False


def test_safe_error_excerpt_omits_non_json_and_limits_output() -> None:
    omitted, omitted_truncated = safe_error_excerpt(
        b"private upstream HTML body",
        "text/html",
    )
    excerpt, truncated = safe_error_excerpt(
        json.dumps({"message": "ordinary long message " * 200}).encode(),
        "application/json",
        limit=128,
    )

    assert omitted is None
    assert omitted_truncated is False
    assert excerpt is not None
    assert len(excerpt) == 128
    assert truncated is True


def test_safe_error_excerpt_redacts_unlabelled_base64_and_never_raises_on_nan() -> None:
    base64_blob = "A" * 256
    excerpt, truncated = safe_error_excerpt(
        json.dumps({"message": f"echoed payload {base64_blob}"}).encode(),
        "application/json",
    )
    invalid, invalid_truncated = safe_error_excerpt(
        b'{"value":NaN}',
        "application/json",
    )

    assert excerpt is not None
    assert "<redacted>" in excerpt
    assert base64_blob not in excerpt
    assert truncated is False
    assert invalid is None
    assert invalid_truncated is False


def test_short_and_request_refs_are_safe_and_request_refs_are_unique() -> None:
    value = "full-sensitive-identifier"
    first = new_request_ref()
    second = new_request_ref()

    assert short_ref(value) == short_ref(value)
    assert len(short_ref(value)) == 8
    assert value not in short_ref(value)
    assert len(first) == 8
    assert len(second) == 8
    assert first != second


def test_logged_stage_records_safe_completion_and_failure(
    caplog: pytest.LogCaptureFixture,
) -> None:
    secret_message = "private stage exception detail"

    with (
        caplog.at_level(logging.INFO, logger="comiclens.events"),
        task_log_context(page_index=4),
    ):
        with logged_stage("ocr", input_bytes=123) as summary:
            summary.update(blocks=2, output_bytes=456)
        with pytest.raises(RuntimeError), logged_stage("translation", blocks=2):
            raise RuntimeError(secret_message)

    messages = [record.getMessage() for record in caplog.records]
    assert messages[0].startswith("task event=stage_start stage=ocr input_bytes=123")
    assert messages[1].startswith("task event=stage_complete stage=ocr duration_ms=")
    assert "blocks=2" in messages[1]
    assert "output_bytes=456" in messages[1]
    assert "page_index=4" in messages[1]
    assert messages[2].startswith("task event=stage_start stage=translation blocks=2")
    assert messages[3].startswith("task event=stage_failed stage=translation duration_ms=")
    assert "error=RuntimeError" in messages[3]
    assert secret_message not in messages[3]
