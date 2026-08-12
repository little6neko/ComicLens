from __future__ import annotations

import asyncio
import base64
import json
import math
import re
import time
from collections.abc import Iterable
from typing import Any
from urllib.parse import urlparse

import httpx

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
        job_model: str = "PaddleOCR-VL-1.5",
        job_poll_interval: float = 2.0,
        job_timeout: float = 180.0,
        concurrency: int = 1,
    ) -> None:
        self.client = client
        self.api_url = api_url.rstrip("/")
        self.token = token.strip()
        self.auth_mode = (auth_mode or "none").strip().lower()
        self.basic_username = basic_username
        self.basic_password = basic_password
        self.mode = (mode or "auto").strip().lower()
        self.job_model = job_model.strip() or "PaddleOCR-VL-1.5"
        self.job_poll_interval = max(0.2, job_poll_interval)
        self.job_timeout = max(self.job_poll_interval, job_timeout)
        self.concurrency = max(1, concurrency)
        self.semaphore = asyncio.Semaphore(self.concurrency)

    async def analyze_image(self, image_bytes: bytes) -> dict[str, Any]:
        async with self.semaphore:
            if self._uses_async_jobs():
                return await self._analyze_image_by_job(image_bytes)
            return await self._analyze_image_direct(image_bytes)

    def _uses_async_jobs(self) -> bool:
        if self.mode == "job":
            return True
        if self.mode == "direct":
            return False
        return "/api/v2/ocr/jobs" in self.api_url or self.api_url.endswith("/ocr/jobs")

    async def _analyze_image_direct(self, image_bytes: bytes) -> dict[str, Any]:
        headers, auth = self._build_request_auth()
        headers["Content-Type"] = "application/json"
        payload = {
            "file": base64.b64encode(image_bytes).decode("ascii"),
            "fileType": 1,
            "useDocOrientationClassify": False,
            "useDocUnwarping": False,
            "useChartRecognition": False,
        }
        response = await self._request(
            "POST",
            self.api_url,
            json=payload,
            headers=headers,
            auth=auth,
        )
        data = response.json()
        if not isinstance(data, dict) or "result" not in data:
            raise ValueError("OCR 返回格式异常，缺少 result")
        return data

    async def _analyze_image_by_job(self, image_bytes: bytes) -> dict[str, Any]:
        optional_payload = {
            "useDocOrientationClassify": False,
            "useDocUnwarping": False,
            "useChartRecognition": False,
        }
        headers, auth = self._build_request_auth()
        data = {
            "model": self.job_model,
            "optionalPayload": json.dumps(optional_payload, ensure_ascii=False),
        }
        files = {"file": ("image.png", image_bytes, "image/png")}
        submit_response = await self._request(
            "POST",
            self.api_url,
            headers=headers,
            data=data,
            files=files,
            auth=auth,
        )
        submit_payload = submit_response.json()
        job_id = (
            submit_payload.get("data", {}).get("jobId")
            if isinstance(submit_payload, dict)
            else None
        )
        if not job_id:
            raise ValueError("OCR 异步任务提交失败，缺少 jobId")

        deadline = time.monotonic() + self.job_timeout
        result_url: str | None = None
        job_url = f"{self.api_url}/{job_id}"
        while time.monotonic() < deadline:
            job_response = await self._request("GET", job_url, headers=headers, auth=auth)
            response_payload = job_response.json()
            payload = response_payload.get("data", {}) if isinstance(response_payload, dict) else {}
            state = str(payload.get("state") or "").lower()
            if state == "done":
                result_urls = payload.get("resultUrl", {})
                if isinstance(result_urls, dict):
                    candidate = result_urls.get("jsonUrl")
                    result_url = str(candidate) if candidate else None
                break
            if state == "failed":
                raise ValueError(str(payload.get("errorMsg") or "OCR 异步任务失败"))
            await asyncio.sleep(self.job_poll_interval)

        if not result_url:
            raise TimeoutError("OCR 异步任务超时")
        self._validate_result_url(result_url)
        result_auth = auth if self._should_send_basic_auth(result_url) else None
        result_response = await self._request("GET", result_url, auth=result_auth)
        entries = [json.loads(line) for line in result_response.text.splitlines() if line.strip()]
        if not entries:
            raise ValueError("OCR 异步任务结果为空")
        if len(entries) == 1:
            if not isinstance(entries[0], dict):
                raise ValueError("OCR 异步任务结果格式异常")
            return entries[0]

        layout_results: list[object] = []
        for entry in entries:
            if not isinstance(entry, dict):
                continue
            root = entry.get("result", entry)
            item_results = root.get("layoutParsingResults") if isinstance(root, dict) else None
            if isinstance(item_results, list):
                layout_results.extend(item_results)
        if layout_results:
            return {"result": {"layoutParsingResults": layout_results}}
        if not isinstance(entries[0], dict):
            raise ValueError("OCR 异步任务结果格式异常")
        return entries[0]

    def _build_request_auth(
        self,
    ) -> tuple[dict[str, str], httpx.BasicAuth | None]:
        if self.auth_mode == "none":
            return {}, None
        if self.auth_mode == "basic":
            if not self.basic_username or not self.basic_password:
                raise ValueError("OCR Basic Auth 缺少用户名或密码")
            return {}, httpx.BasicAuth(self.basic_username, self.basic_password)
        if self.auth_mode in {"bearer", "token"}:
            if not self.token:
                raise ValueError("OCR Token 不能为空")
            scheme = "Bearer" if self.auth_mode == "bearer" else "token"
            return {"Authorization": f"{scheme} {self.token}"}, None
        raise ValueError("OCR 认证模式无效")

    def _should_send_basic_auth(self, target_url: str) -> bool:
        if self.auth_mode != "basic":
            return False
        api_parts = urlparse(self.api_url)
        target_parts = urlparse(target_url)
        return api_parts.scheme == target_parts.scheme and api_parts.netloc == target_parts.netloc

    @staticmethod
    def _validate_result_url(target_url: str) -> None:
        parsed = urlparse(target_url)
        if parsed.scheme not in {"http", "https"} or not parsed.hostname:
            raise ValueError("OCR 结果地址无效")

    async def _request(
        self,
        method: str,
        url: str,
        **kwargs: Any,
    ) -> httpx.Response:
        last_error: Exception | None = None
        for attempt in range(3):
            try:
                response = await self.client.request(method, url, **kwargs)
                if (response.status_code == 429 or response.status_code >= 500) and attempt < 2:
                    await asyncio.sleep(0.2 * (2**attempt))
                    continue
                response.raise_for_status()
                return response
            except (httpx.TimeoutException, httpx.NetworkError) as exc:
                last_error = exc
                if attempt < 2:
                    await asyncio.sleep(0.2 * (2**attempt))
                    continue
                break
            except httpx.HTTPError:
                raise
        assert last_error is not None
        raise last_error


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
