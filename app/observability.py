from __future__ import annotations

import hashlib
import itertools
import json
import logging
import re
import time
from collections.abc import Iterator, Mapping, Sequence
from contextlib import contextmanager
from contextvars import ContextVar
from typing import Any
from urllib.parse import quote, quote_plus, urlsplit

LOG_FORMAT = "%(levelname)s %(message)s"
EVENT_LOGGER = logging.getLogger("comiclens.events")

_CONTROL_PATTERN = re.compile(r"[\x00-\x1f\x7f]+")
_SPACE_PATTERN = re.compile(r"\s+")
_SIMPLE_VALUE_PATTERN = re.compile(r"^[A-Za-z0-9._:/@%+,-]+$")
_URL_PATTERN = re.compile(r"https?://[^\s\"'<>]+", re.IGNORECASE)
_BASE64_BLOB_PATTERN = re.compile(r"[A-Za-z0-9+/]{128,}={0,2}")
_SENSITIVE_KEY_MARKERS = (
    "authorization",
    "token",
    "key",
    "password",
    "secret",
    "cookie",
    "file",
    "image",
    "base64",
    "text",
    "content",
    "translation",
    "result",
    "jobid",
)
_LOG_CONTEXT: ContextVar[Mapping[str, object] | None] = ContextVar(
    "comiclens_log_context",
    default=None,
)
_REQUEST_COUNTER = itertools.count(1)


def format_event(service: str, event: str, **fields: object) -> str:
    parts = [_label(service), f"event={_label(event)}"]
    merged = dict(fields)
    for key, value in _context().items():
        merged.setdefault(key, value)
    for key, value in merged.items():
        if value is None:
            continue
        parts.append(f"{_label(key)}={_format_value(key, value)}")
    return " ".join(parts)


def log_event(
    service: str,
    event: str,
    *,
    level: int = logging.INFO,
    **fields: object,
) -> None:
    EVENT_LOGGER.log(
        level,
        format_event(service, event, **fields),
        stacklevel=2,
    )


def current_log_context() -> dict[str, object]:
    return dict(_context())


@contextmanager
def task_log_context(
    *,
    generation_id: str | None = None,
    comic: str | None = None,
    chapter: str | None = None,
    page_index: int | None = None,
    segment_index: int | None = None,
    global_index: int | None = None,
) -> Iterator[None]:
    context = dict(_context())
    updates: dict[str, object] = {}
    if generation_id is not None:
        updates["generation_ref"] = short_ref(generation_id)
    if comic is not None:
        updates["comic"] = comic
    if chapter is not None:
        updates["chapter"] = chapter
    if page_index is not None:
        updates["page_index"] = page_index
    if segment_index is not None:
        updates["segment_index"] = segment_index
    if global_index is not None:
        updates["global_index"] = global_index
    context.update(updates)
    token = _LOG_CONTEXT.set(context)
    try:
        yield
    finally:
        _LOG_CONTEXT.reset(token)


@contextmanager
def logged_stage(stage: str, **fields: object) -> Iterator[dict[str, object]]:
    started = time.monotonic()
    log_event("task", "stage_start", stage=stage, **fields)
    summary: dict[str, object] = {}
    try:
        yield summary
    except BaseException as exc:
        log_event(
            "task",
            "stage_failed",
            level=logging.ERROR,
            stage=stage,
            duration_ms=round((time.monotonic() - started) * 1000),
            error=getattr(exc, "code", None) or type(exc).__name__,
        )
        raise
    else:
        log_event(
            "task",
            "stage_complete",
            stage=stage,
            duration_ms=round((time.monotonic() - started) * 1000),
            **summary,
        )


def new_request_ref() -> str:
    return f"{next(_REQUEST_COUNTER) & 0xFFFFFFFF:08x}"


def short_ref(value: object, *, length: int = 8) -> str:
    digest = hashlib.sha256(str(value).encode("utf-8")).hexdigest()
    return digest[: max(4, min(length, len(digest)))]


def safe_endpoint(
    url: str,
    *,
    secrets: Sequence[str] = (),
    origin_only: bool = False,
    limit: int = 300,
) -> str:
    try:
        parsed = urlsplit(str(url))
        scheme = parsed.scheme.lower()
        hostname = parsed.hostname
        port = parsed.port
    except (TypeError, ValueError):
        return "<invalid-url>"
    if scheme not in {"http", "https"} or not hostname:
        return "<invalid-url>"

    host = hostname.lower()
    if ":" in host:
        host = f"[{host}]"
    default_port = 443 if scheme == "https" else 80
    authority = host if port in {None, default_port} else f"{host}:{port}"
    endpoint = f"{scheme}://{authority}"
    if not origin_only:
        endpoint += parsed.path or ""
    endpoint = _replace_literals(endpoint, secrets)
    return _truncate(_single_line(endpoint), limit)


def safe_error_excerpt(
    content: bytes,
    content_type: str,
    *,
    secrets: Sequence[str] = (),
    sensitive_texts: Sequence[str] = (),
    limit: int = 1024,
) -> tuple[str | None, bool]:
    if "json" not in content_type.lower():
        return None, False
    try:
        payload = json.loads(content.decode("utf-8"))
        redacted = _redact_json(
            payload,
            sensitive_literals=tuple(secrets) + tuple(sensitive_texts),
        )
        serialized = json.dumps(
            redacted,
            ensure_ascii=False,
            allow_nan=False,
            separators=(",", ":"),
        )
    except (UnicodeDecodeError, json.JSONDecodeError, RecursionError, TypeError, ValueError):
        return None, False
    serialized = _single_line(serialized)
    truncated = len(serialized) > limit
    return serialized[:limit], truncated


def _redact_json(value: Any, *, sensitive_literals: Sequence[str]) -> Any:
    if isinstance(value, dict):
        output: dict[str, Any] = {}
        for key, item in value.items():
            normalized_key = re.sub(r"[^a-z0-9]", "", str(key).lower())
            if any(marker in normalized_key for marker in _SENSITIVE_KEY_MARKERS):
                output[str(key)] = "<redacted>"
            else:
                output[str(key)] = _redact_json(
                    item,
                    sensitive_literals=sensitive_literals,
                )
        return output
    if isinstance(value, list):
        return [_redact_json(item, sensitive_literals=sensitive_literals) for item in value]
    if isinstance(value, str):
        redacted = _URL_PATTERN.sub(lambda match: safe_endpoint(match.group(0)), value)
        redacted = _replace_literals(redacted, sensitive_literals)
        redacted = _BASE64_BLOB_PATTERN.sub("<redacted>", redacted)
        return _single_line(redacted)
    return value


def _replace_literals(value: str, literals: Sequence[str]) -> str:
    output = value
    variants: set[str] = set()
    for literal in literals:
        text = str(literal)
        if not text:
            continue
        variants.update({text, quote(text, safe=""), quote_plus(text, safe="")})
    for variant in sorted(variants, key=len, reverse=True):
        output = output.replace(variant, "<redacted>")
    return output


def _format_value(key: str, value: object) -> str:
    if isinstance(value, bool):
        return "true" if value else "false"
    if isinstance(value, int | float):
        return str(value)
    if isinstance(value, str):
        cleaned = _truncate(_single_line(value), 1024 if key == "excerpt" else 300)
    else:
        cleaned = _truncate(
            _single_line(json.dumps(value, ensure_ascii=False, default=str)),
            300,
        )
    if _SIMPLE_VALUE_PATTERN.fullmatch(cleaned):
        return cleaned
    return json.dumps(cleaned, ensure_ascii=False)


def _label(value: object) -> str:
    normalized = re.sub(r"[^A-Za-z0-9_.-]+", "_", str(value).strip())
    return normalized or "unknown"


def _single_line(value: str) -> str:
    return _SPACE_PATTERN.sub(" ", _CONTROL_PATTERN.sub(" ", value)).strip()


def _truncate(value: str, limit: int) -> str:
    if len(value) <= limit:
        return value
    if limit <= 1:
        return value[:limit]
    return f"{value[: limit - 1]}…"


def _context() -> Mapping[str, object]:
    return _LOG_CONTEXT.get() or {}
