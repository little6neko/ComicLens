from __future__ import annotations

import asyncio
import base64
import json
import logging
import math
import re
import time
from collections.abc import Iterable
from typing import Any, Literal, Protocol
from urllib.parse import urlparse

import httpx

from app.observability import (
    EVENT_LOGGER,
    log_event,
    new_request_ref,
    safe_endpoint,
    safe_error_excerpt,
    short_ref,
)
from app.translation.concurrency import DynamicConcurrencyLimiter
from app.translation.models import TextBlock

TEXT_KEYS = (
    "text",
    "content",
    "value",
    "block_content",
    "recognizedText",
    "ocrText",
)

BOX_KEYS = (
    "bbox",
    "block_bbox",
    "box",
    "boundingBox",
    "bounding_box",
    "block_polygon_points",
    "rect",
    "region",
    "polygon",
    "points",
    "vertices",
    "quad",
    "position",
    "coordinate",
    "coordinates",
)


class OCRProtocolError(ValueError):
    """Raised when the cloud OCR service returns an unusable response."""


class OCRJobFailedError(ValueError):
    """Raised when the cloud OCR service reports a failed job."""


class OCRJobNotFoundError(ValueError):
    """Raised when a persisted cloud OCR job no longer exists."""


class OCRJobObserver(Protocol):
    def __call__(self, job_id: str) -> None: ...


OCRProtocol = Literal["direct", "job"]


def resolve_ocr_protocol(mode: str, api_url: str) -> OCRProtocol:
    normalized_mode = (mode or "auto").strip().lower()
    if normalized_mode == "direct":
        return "direct"
    if normalized_mode == "job":
        return "job"
    if normalized_mode != "auto":
        raise ValueError("OCR 模式无效")

    path = urlparse(api_url).path.rstrip("/")
    if path.endswith("/ocr/jobs"):
        return "job"
    return "direct"


class OCRClient:
    def __init__(
        self,
        client: httpx.AsyncClient,
        api_url: str,
        token: str = "",
        auth_mode: str = "bearer",
        basic_username: str = "",
        basic_password: str = "",
        mode: str = "auto",
        job_model: str = "PaddleOCR-VL-1.6",
        job_poll_interval: float = 2.0,
        job_timeout: float = 180.0,
        concurrency: int = 1,
        request_timeout: float = 180.0,
        limiter: DynamicConcurrencyLimiter | None = None,
    ) -> None:
        self.client = client
        self.api_url = api_url.rstrip("/")
        self.token = token.strip()
        self.auth_mode = (auth_mode or "none").strip().lower()
        self.basic_username = basic_username
        self.basic_password = basic_password
        self.protocol = resolve_ocr_protocol(mode, self.api_url)
        self.job_model = job_model.strip() or "PaddleOCR-VL-1.6"
        self.job_poll_interval = max(0.2, job_poll_interval)
        self.job_timeout = max(self.job_poll_interval, job_timeout)
        self.request_timeout = max(1.0, request_timeout)
        self.limiter = limiter or DynamicConcurrencyLimiter(max(1, concurrency))

    @property
    def concurrency(self) -> int:
        return self.limiter.limit

    async def analyze_image(
        self,
        image_bytes: bytes,
        *,
        job_id: str | None = None,
        on_job_submitted: OCRJobObserver | None = None,
    ) -> dict[str, Any]:
        async with self.limiter.slot():
            if self.protocol == "direct":
                return await self._analyze_image_direct(image_bytes)
            return await self._analyze_image_by_job(
                image_bytes,
                job_id=job_id,
                on_job_submitted=on_job_submitted,
            )

    async def _analyze_image_direct(self, image_bytes: bytes) -> dict[str, Any]:
        headers, auth = self._build_request_auth()
        headers["Content-Type"] = "application/json"
        encoded_image = base64.b64encode(image_bytes).decode("ascii")
        payload = {
            "file": encoded_image,
            "fileType": 1,
            "useDocOrientationClassify": False,
            "useDocUnwarping": False,
            "useChartRecognition": False,
            "visualize": False,
        }
        response = await self._request(
            "POST",
            self.api_url,
            headers=headers,
            auth=auth,
            json=payload,
            operation="analyze",
            request_fields={
                "image_bytes": len(image_bytes),
                "payload_bytes": self._json_bytes(payload),
            },
        )
        response_payload = self._response_object_logged(
            response,
            "OCR 同步响应",
            operation="analyze",
        )
        if "result" not in response_payload:
            self._log_protocol_failure(response, operation="analyze")
            raise OCRProtocolError("OCR 同步响应缺少 result")
        log_event(
            "ocr",
            "completed",
            operation="analyze",
            protocol=self.protocol,
            layout_results=self._layout_result_count(response_payload),
        )
        return response_payload

    async def _analyze_image_by_job(
        self,
        image_bytes: bytes,
        *,
        job_id: str | None = None,
        on_job_submitted: OCRJobObserver | None = None,
    ) -> dict[str, Any]:
        optional_payload = {
            "useDocOrientationClassify": False,
            "useDocUnwarping": False,
            "useChartRecognition": False,
        }
        headers, auth = self._build_request_auth()
        if not job_id:
            optional_payload_json = json.dumps(optional_payload, ensure_ascii=False)
            data = {
                "model": self.job_model,
                "optionalPayload": optional_payload_json,
            }
            files = {"file": ("image.png", image_bytes, "image/png")}
            submit_response = await self._request(
                "POST",
                self.api_url,
                headers=headers,
                auth=auth,
                data=data,
                files=files,
                operation="submit",
                request_fields={
                    "model": self.job_model,
                    "image_bytes": len(image_bytes),
                    "payload_bytes": (
                        len(self.job_model.encode("utf-8"))
                        + len(optional_payload_json.encode("utf-8"))
                        + len(image_bytes)
                    ),
                },
            )
            submit_payload = self._response_object_logged(
                submit_response,
                "OCR 异步任务提交响应",
                operation="submit",
            )
            submit_data = submit_payload.get("data")
            candidate = submit_data.get("jobId") if isinstance(submit_data, dict) else None
            job_id = str(candidate) if candidate else None
            if not job_id:
                self._log_protocol_failure(submit_response, operation="submit")
                raise OCRProtocolError("OCR 异步任务提交响应缺少 jobId")
            log_event(
                "ocr",
                "job_submitted",
                operation="submit",
                protocol=self.protocol,
                model=self.job_model,
                job_ref=short_ref(job_id),
            )
            if on_job_submitted is not None:
                on_job_submitted(job_id)

        deadline = time.monotonic() + self.job_timeout
        result_url: str | None = None
        job_url = f"{self.api_url}/{job_id}"
        job_ref = short_ref(job_id)
        last_logged_state: str | None = None
        poll_count = 0
        last_job_response: httpx.Response | None = None
        while time.monotonic() < deadline:
            poll_count += 1
            try:
                job_response = await self._request(
                    "GET",
                    job_url,
                    headers=headers,
                    auth=auth,
                    operation="poll",
                    log_level=logging.DEBUG,
                    endpoint_secrets=(job_id,),
                    request_fields={
                        "job_ref": job_ref,
                        "poll_count": poll_count,
                    },
                )
            except httpx.HTTPStatusError as exc:
                if exc.response.status_code in {404, 410}:
                    raise OCRJobNotFoundError("OCR 异步任务已失效") from exc
                raise
            last_job_response = job_response
            response_payload = self._response_object_logged(
                job_response,
                "OCR 异步任务状态响应",
                operation="poll",
                job_ref=job_ref,
            )
            payload = response_payload.get("data")
            if not isinstance(payload, dict):
                self._log_protocol_failure(
                    job_response,
                    operation="poll",
                    job_ref=job_ref,
                )
                raise OCRProtocolError("OCR 异步任务状态响应缺少 data")
            state = str(payload.get("state") or "").lower()
            logged_state = state if state in {"pending", "running", "done", "failed"} else "invalid"
            if logged_state != last_logged_state:
                log_event(
                    "ocr",
                    "state",
                    operation="poll",
                    protocol=self.protocol,
                    job_ref=job_ref,
                    state=logged_state,
                    poll_count=poll_count,
                )
                last_logged_state = logged_state
            if state == "done":
                result_urls = payload.get("resultUrl", {})
                if isinstance(result_urls, dict):
                    candidate = result_urls.get("jsonUrl")
                    result_url = str(candidate) if candidate else None
                if not result_url:
                    self._log_protocol_failure(
                        job_response,
                        operation="poll",
                        job_ref=job_ref,
                    )
                    raise OCRProtocolError("OCR 异步任务结果缺少 jsonUrl")
                break
            if state == "failed":
                log_event(
                    "ocr",
                    "failed",
                    level=logging.ERROR,
                    operation="poll",
                    protocol=self.protocol,
                    job_ref=job_ref,
                    error="OCRJobFailedError",
                    poll_count=poll_count,
                )
                raise OCRJobFailedError(str(payload.get("errorMsg") or "OCR 异步任务失败"))
            if state not in {"pending", "running"}:
                self._log_protocol_failure(
                    job_response,
                    operation="poll",
                    job_ref=job_ref,
                )
                raise OCRProtocolError(f"OCR 异步任务状态无效: {state or 'missing'}")
            await asyncio.sleep(self.job_poll_interval)

        if not result_url:
            log_event(
                "ocr",
                "failed",
                level=logging.ERROR,
                operation="poll",
                protocol=self.protocol,
                job_ref=job_ref,
                error="TimeoutError",
                poll_count=poll_count,
            )
            raise TimeoutError("OCR 异步任务超时")
        try:
            self._validate_result_url(result_url)
        except OCRProtocolError:
            if last_job_response is not None:
                self._log_protocol_failure(
                    last_job_response,
                    operation="poll",
                    job_ref=job_ref,
                )
            raise
        result_auth = auth if self._should_send_basic_auth(result_url) else None
        result_response = await self._request(
            "GET",
            result_url,
            auth=result_auth,
            operation="download_result",
            endpoint_origin_only=True,
            auth_label="basic" if result_auth is not None else "none",
            request_fields={"job_ref": job_ref},
        )
        try:
            result = self._parse_job_result(result_response)
        except OCRProtocolError:
            self._log_protocol_failure(
                result_response,
                operation="download_result",
                job_ref=job_ref,
            )
            raise
        log_event(
            "ocr",
            "completed",
            operation="download_result",
            protocol=self.protocol,
            job_ref=job_ref,
            layout_results=self._layout_result_count(result),
        )
        return result

    @staticmethod
    def _parse_job_result(result_response: httpx.Response) -> dict[str, Any]:
        entries: list[dict[str, Any]] = []
        for line_number, line in enumerate(result_response.text.splitlines(), start=1):
            if not line.strip():
                continue
            try:
                entry = json.loads(line)
            except (json.JSONDecodeError, UnicodeDecodeError) as exc:
                raise OCRProtocolError(f"OCR JSONL 第 {line_number} 行格式异常") from exc
            if not isinstance(entry, dict):
                raise OCRProtocolError(f"OCR JSONL 第 {line_number} 行不是对象")
            entries.append(entry)
        if not entries:
            raise OCRProtocolError("OCR 异步任务结果为空")

        layout_results: list[object] = []
        for line_number, entry in enumerate(entries, start=1):
            root = entry.get("result")
            if not isinstance(root, dict):
                raise OCRProtocolError(f"OCR JSONL 第 {line_number} 行缺少 result")
            item_results = root.get("layoutParsingResults")
            if not isinstance(item_results, list):
                raise OCRProtocolError(f"OCR JSONL 第 {line_number} 行缺少 layoutParsingResults")
            layout_results.extend(item_results)
        return {"result": {"layoutParsingResults": layout_results}}

    def _build_request_auth(self) -> tuple[dict[str, str], httpx.Auth | None]:
        if self.auth_mode == "none":
            return {}, None
        if self.auth_mode == "bearer":
            if not self.token:
                raise ValueError("OCR Token 不能为空")
            return {"Authorization": f"Bearer {self.token}"}, None
        if self.auth_mode == "basic":
            if not self.basic_username.strip() or not self.basic_password.strip():
                raise ValueError("OCR Basic Auth 缺少用户名或密码")
            return {}, httpx.BasicAuth(self.basic_username, self.basic_password)
        raise ValueError("OCR 认证模式无效")

    def _should_send_basic_auth(self, target_url: str) -> bool:
        if self.auth_mode != "basic":
            return False
        api_origin = self._origin(self.api_url)
        return api_origin is not None and api_origin == self._origin(target_url)

    @staticmethod
    def _origin(target_url: str) -> tuple[str, str, int] | None:
        parsed = urlparse(target_url)
        scheme = parsed.scheme.lower()
        if scheme not in {"http", "https"} or not parsed.hostname:
            return None
        try:
            port = parsed.port
        except ValueError:
            return None
        if port is None:
            port = 443 if scheme == "https" else 80
        return scheme, parsed.hostname.lower(), port

    @staticmethod
    def _response_object(response: httpx.Response, context: str) -> dict[str, Any]:
        try:
            payload = response.json()
        except (json.JSONDecodeError, UnicodeDecodeError) as exc:
            raise OCRProtocolError(f"{context}不是有效 JSON") from exc
        if not isinstance(payload, dict):
            raise OCRProtocolError(f"{context}不是对象")
        return payload

    def _response_object_logged(
        self,
        response: httpx.Response,
        context: str,
        *,
        operation: str,
        job_ref: str | None = None,
        sensitive_texts: Iterable[str] = (),
    ) -> dict[str, Any]:
        try:
            return self._response_object(response, context)
        except OCRProtocolError:
            self._log_protocol_failure(
                response,
                operation=operation,
                job_ref=job_ref,
                sensitive_texts=sensitive_texts,
            )
            raise

    def _log_protocol_failure(
        self,
        response: httpx.Response,
        *,
        operation: str,
        job_ref: str | None = None,
        sensitive_texts: Iterable[str] = (),
    ) -> None:
        log_event(
            "ocr",
            "failed",
            level=logging.ERROR,
            operation=operation,
            request_ref=response.extensions.get("comiclens_request_ref"),
            protocol=self.protocol,
            job_ref=job_ref,
            status=response.status_code,
            attempt=response.extensions.get("comiclens_attempt"),
            error="OCRProtocolError",
        )
        self._log_error_detail(
            response,
            operation=operation,
            request_ref=response.extensions.get("comiclens_request_ref"),
            secrets=self._redaction_secrets(),
            sensitive_texts=sensitive_texts,
            job_ref=job_ref,
        )

    @staticmethod
    def _json_bytes(payload: object) -> int:
        return len(
            json.dumps(
                payload,
                ensure_ascii=False,
                separators=(",", ":"),
            ).encode("utf-8")
        )

    @staticmethod
    def _layout_result_count(payload: dict[str, Any]) -> int:
        root = payload.get("result")
        if not isinstance(root, dict):
            return 0
        results = root.get("layoutParsingResults")
        return len(results) if isinstance(results, list) else 0

    @staticmethod
    def _validate_result_url(target_url: str) -> None:
        parsed = urlparse(target_url)
        if parsed.scheme not in {"http", "https"} or not parsed.hostname:
            raise OCRProtocolError("OCR 结果地址无效")

    async def _request(
        self,
        method: str,
        url: str,
        *,
        operation: str,
        log_level: int = logging.INFO,
        endpoint_origin_only: bool = False,
        endpoint_secrets: Iterable[str] = (),
        auth_label: str | None = None,
        request_fields: dict[str, object] | None = None,
        sensitive_texts: Iterable[str] = (),
        **kwargs: Any,
    ) -> httpx.Response:
        request_ref = new_request_ref()
        secrets = self._redaction_secrets(endpoint_secrets)
        endpoint = safe_endpoint(
            url,
            secrets=secrets,
            origin_only=endpoint_origin_only,
        )
        fields = {
            "operation": operation,
            "request_ref": request_ref,
            "protocol": self.protocol,
            "auth": auth_label or self.auth_mode,
            **(request_fields or {}),
        }
        last_error: Exception | None = None
        total_started = time.monotonic()
        for attempt in range(3):
            log_event(
                "ocr",
                "request",
                level=log_level,
                **fields,
                method=method.upper(),
                endpoint=endpoint,
                attempt=attempt + 1,
            )
            attempt_started = time.monotonic()
            try:
                response = await self.client.request(
                    method,
                    url,
                    timeout=self.request_timeout,
                    **kwargs,
                )
                response.extensions["comiclens_request_ref"] = request_ref
                response.extensions["comiclens_attempt"] = attempt + 1
                duration_ms = round((time.monotonic() - attempt_started) * 1000)
                content_type = response.headers.get("content-type", "").split(";", 1)[0]
                log_event(
                    "ocr",
                    "response",
                    level=log_level,
                    **fields,
                    status=response.status_code,
                    duration_ms=duration_ms,
                    response_bytes=len(response.content),
                    content_type=content_type or None,
                    endpoint=endpoint,
                    attempt=attempt + 1,
                )
                if response.status_code >= 400:
                    self._log_error_detail(
                        response,
                        operation=operation,
                        request_ref=request_ref,
                        secrets=secrets,
                        sensitive_texts=sensitive_texts,
                        job_ref=(request_fields or {}).get("job_ref"),
                    )
                if (response.status_code == 429 or response.status_code >= 500) and attempt < 2:
                    delay = 0.2 * (2**attempt)
                    log_event(
                        "ocr",
                        "retry",
                        level=logging.WARNING,
                        operation=operation,
                        request_ref=request_ref,
                        protocol=self.protocol,
                        status=response.status_code,
                        attempt=attempt + 1,
                        next_attempt=attempt + 2,
                        delay_ms=round(delay * 1000),
                        job_ref=(request_fields or {}).get("job_ref"),
                    )
                    await asyncio.sleep(delay)
                    continue
                response.raise_for_status()
                return response
            except (httpx.TimeoutException, httpx.NetworkError) as exc:
                last_error = exc
                if attempt < 2:
                    delay = 0.2 * (2**attempt)
                    log_event(
                        "ocr",
                        "retry",
                        level=logging.WARNING,
                        operation=operation,
                        request_ref=request_ref,
                        protocol=self.protocol,
                        attempt=attempt + 1,
                        next_attempt=attempt + 2,
                        delay_ms=round(delay * 1000),
                        error=type(exc).__name__,
                        job_ref=(request_fields or {}).get("job_ref"),
                    )
                    await asyncio.sleep(delay)
                    continue
                break
            except httpx.HTTPError as exc:
                self._log_request_failure(
                    operation=operation,
                    request_ref=request_ref,
                    endpoint=endpoint,
                    error=exc,
                    attempts=attempt + 1,
                    total_started=total_started,
                    job_ref=(request_fields or {}).get("job_ref"),
                )
                raise
        assert last_error is not None
        self._log_request_failure(
            operation=operation,
            request_ref=request_ref,
            endpoint=endpoint,
            error=last_error,
            attempts=3,
            total_started=total_started,
            job_ref=(request_fields or {}).get("job_ref"),
        )
        raise last_error

    def _redaction_secrets(self, additional: Iterable[str] = ()) -> tuple[str, ...]:
        return tuple(
            value
            for value in (
                self.token,
                self.basic_username,
                self.basic_password,
                *additional,
            )
            if value
        )

    def _log_error_detail(
        self,
        response: httpx.Response,
        *,
        operation: str,
        request_ref: object,
        secrets: Iterable[str],
        sensitive_texts: Iterable[str] = (),
        job_ref: object = None,
    ) -> None:
        if not EVENT_LOGGER.isEnabledFor(logging.DEBUG):
            return
        excerpt, truncated = safe_error_excerpt(
            response.content,
            response.headers.get("content-type", ""),
            secrets=tuple(secrets),
            sensitive_texts=tuple(sensitive_texts),
        )
        if excerpt is None:
            return
        log_event(
            "ocr",
            "error_detail",
            level=logging.DEBUG,
            operation=operation,
            request_ref=request_ref,
            protocol=self.protocol,
            job_ref=job_ref,
            status=response.status_code,
            excerpt=excerpt,
            truncated=truncated,
        )

    def _log_request_failure(
        self,
        *,
        operation: str,
        request_ref: str,
        endpoint: str,
        error: httpx.HTTPError,
        attempts: int,
        total_started: float,
        job_ref: object = None,
    ) -> None:
        status = error.response.status_code if isinstance(error, httpx.HTTPStatusError) else None
        log_event(
            "ocr",
            "failed",
            level=logging.ERROR,
            operation=operation,
            request_ref=request_ref,
            protocol=self.protocol,
            job_ref=job_ref,
            endpoint=endpoint,
            status=status,
            attempts=attempts,
            duration_ms=round((time.monotonic() - total_started) * 1000),
            error=type(error).__name__,
        )


def extract_text_blocks(
    ocr_payload: dict[str, Any], image_size: tuple[int, int]
) -> list[TextBlock]:
    root = ocr_payload.get("result", ocr_payload)
    if not isinstance(root, dict):
        return []
    layout_results = root.get("layoutParsingResults")
    nodes = layout_results if isinstance(layout_results, list) else [root]

    seen: set[tuple[str, tuple[int, int, int, int]]] = set()
    blocks: list[TextBlock] = []
    for page_index, node in enumerate(nodes):
        _walk_blocks(
            node=node,
            image_size=image_size,
            seen=seen,
            output=blocks,
            path=f"layoutParsingResults[{page_index}]",
        )

    filtered = [block for block in blocks if _is_meaningful_block(block, image_size=image_size)]
    filtered.sort(key=lambda item: (item.bbox[1], item.bbox[0]))
    return filtered


def _walk_blocks(
    node: Any,
    image_size: tuple[int, int],
    seen: set[tuple[str, tuple[int, int, int, int]]],
    output: list[TextBlock],
    path: str,
) -> None:
    if isinstance(node, dict):
        text = _extract_text(node)
        bbox = _extract_bbox(node, image_size=image_size)
        if text and bbox:
            key = (text, bbox)
            if key not in seen:
                seen.add(key)
                output.append(
                    TextBlock(
                        text=text,
                        bbox=bbox,
                        confidence=_extract_confidence(node),
                        source_path=path,
                    )
                )
        for key, value in node.items():
            if key in {"markdown", "images", "outputImages"}:
                continue
            _walk_blocks(
                node=value,
                image_size=image_size,
                seen=seen,
                output=output,
                path=f"{path}.{key}",
            )
        return

    if isinstance(node, list):
        for index, item in enumerate(node):
            _walk_blocks(
                node=item,
                image_size=image_size,
                seen=seen,
                output=output,
                path=f"{path}[{index}]",
            )


def _extract_text(node: dict[str, Any]) -> str | None:
    for key in TEXT_KEYS:
        value = node.get(key)
        if isinstance(value, str):
            text = re.sub(r"\s+", " ", value).strip()
            if text:
                return text
    return None


def _extract_confidence(node: dict[str, Any]) -> float | None:
    for key in ("confidence", "score", "probability"):
        value = node.get(key)
        if isinstance(value, int | float):
            return float(value)
    return None


def _extract_bbox(
    node: dict[str, Any], image_size: tuple[int, int]
) -> tuple[int, int, int, int] | None:
    for key in BOX_KEYS:
        value = node.get(key)
        if value is None:
            continue
        bbox = _coerce_bbox(value, image_size=image_size, key_hint=key)
        if bbox:
            return bbox

    for key in ("location", "geometry", "shape", "area"):
        value = node.get(key)
        if isinstance(value, dict):
            bbox = _extract_bbox(value, image_size=image_size)
            if bbox:
                return bbox
    return None


def _coerce_bbox(
    value: Any,
    image_size: tuple[int, int],
    key_hint: str = "",
) -> tuple[int, int, int, int] | None:
    if isinstance(value, dict):
        if {"left", "top", "right", "bottom"} <= set(value):
            return _normalize_bbox(
                (value["left"], value["top"], value["right"], value["bottom"]),
                image_size=image_size,
                coordinate_mode="xyxy",
            )
        if {"x1", "y1", "x2", "y2"} <= set(value):
            return _normalize_bbox(
                (value["x1"], value["y1"], value["x2"], value["y2"]),
                image_size=image_size,
                coordinate_mode="xyxy",
            )
        if {"x", "y", "width", "height"} <= set(value):
            return _normalize_bbox(
                (value["x"], value["y"], value["width"], value["height"]),
                image_size=image_size,
                coordinate_mode="xywh",
            )
        for nested_key in BOX_KEYS:
            if nested_key in value:
                nested_bbox = _coerce_bbox(
                    value[nested_key],
                    image_size=image_size,
                    key_hint=nested_key,
                )
                if nested_bbox:
                    return nested_bbox
        return None

    if isinstance(value, list | tuple):
        if len(value) == 4 and all(isinstance(item, int | float) for item in value):
            mode = "xywh" if key_hint in {"rect", "region", "position"} else "xyxy"
            return _normalize_bbox(value, image_size=image_size, coordinate_mode=mode)
        if (
            len(value) >= 4
            and len(value) % 2 == 0
            and all(isinstance(item, int | float) for item in value)
        ):
            pairs = list(zip(value[0::2], value[1::2], strict=True))
            return _polygon_to_bbox(pairs, image_size=image_size)

        points: list[tuple[float, float]] = []
        for item in value:
            point = _coerce_point(item)
            if point:
                points.append(point)
        if points:
            return _polygon_to_bbox(points, image_size=image_size)
    return None


def _coerce_point(value: Any) -> tuple[float, float] | None:
    if isinstance(value, dict):
        if {"x", "y"} <= set(value):
            return float(value["x"]), float(value["y"])
        if {"left", "top"} <= set(value):
            return float(value["left"]), float(value["top"])
    if isinstance(value, list | tuple) and len(value) >= 2:
        x, y = value[:2]
        if isinstance(x, int | float) and isinstance(y, int | float):
            return float(x), float(y)
    return None


def _polygon_to_bbox(
    points: Iterable[tuple[float, float]],
    image_size: tuple[int, int],
) -> tuple[int, int, int, int] | None:
    points = list(points)
    if not points:
        return None
    xs = [point[0] for point in points]
    ys = [point[1] for point in points]
    return _normalize_bbox(
        (min(xs), min(ys), max(xs), max(ys)),
        image_size=image_size,
        coordinate_mode="xyxy",
    )


def _normalize_bbox(
    coords: Iterable[float],
    image_size: tuple[int, int],
    coordinate_mode: str,
) -> tuple[int, int, int, int] | None:
    width, height = image_size
    coordinate_values = list(coords)
    if len(coordinate_values) < 4:
        return None
    x1, y1, x2, y2 = [float(value) for value in coordinate_values[:4]]

    normalized = all(0 <= value <= 1 for value in (x1, y1, x2, y2))
    if normalized:
        if coordinate_mode == "xywh":
            x1, y1, x2, y2 = (
                x1 * width,
                y1 * height,
                (x1 + x2) * width,
                (y1 + y2) * height,
            )
        else:
            x1, y1, x2, y2 = (
                x1 * width,
                y1 * height,
                x2 * width,
                y2 * height,
            )
    elif coordinate_mode == "xywh":
        x1, y1, x2, y2 = x1, y1, x1 + x2, y1 + y2

    if x2 <= x1 or y2 <= y1:
        return None
    x1 = max(0, min(width - 1, math.floor(x1)))
    y1 = max(0, min(height - 1, math.floor(y1)))
    x2 = max(1, min(width, math.ceil(x2)))
    y2 = max(1, min(height, math.ceil(y2)))
    if x2 - x1 < 8 or y2 - y1 < 8:
        return None
    return int(x1), int(y1), int(x2), int(y2)


def _is_meaningful_block(block: TextBlock, image_size: tuple[int, int]) -> bool:
    text = block.text.strip()
    if not text or text.lower().startswith("http"):
        return False
    if re.fullmatch(r"[\W_]+", text) or len(text) > 180:
        return False

    page_area = image_size[0] * image_size[1]
    x1, y1, x2, y2 = block.bbox
    bbox_area = (x2 - x1) * (y2 - y1)
    return not (page_area and bbox_area / page_area > 0.7 and len(text) > 30)
