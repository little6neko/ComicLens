from __future__ import annotations

import asyncio
import json
import logging
import time
from collections.abc import Iterable
from typing import Any

import httpx

from app.observability import (
    EVENT_LOGGER,
    log_event,
    new_request_ref,
    safe_endpoint,
    safe_error_excerpt,
)

DEEPL_FREE_URL = "https://api-free.deepl.com/v2/translate"
DEEPL_PRO_URL = "https://api.deepl.com/v2/translate"
DEEPL_MAX_TEXTS = 50
DEEPL_MAX_REQUEST_BYTES = 128 * 1024


class TranslationProtocolError(ValueError):
    """Raised when a translation service returns an unusable response."""


class TranslationInputTooLargeError(ValueError):
    """Raised when one source text cannot fit in a DeepL request."""


class DeepLAuthenticationError(ValueError):
    """Raised when DeepL rejects an API key."""


class DeepLQuotaExceededError(ValueError):
    """Raised when a DeepL account has exhausted its quota."""


class DeepLRateLimitError(ValueError):
    """Raised when DeepL keeps rate-limiting a request after retries."""


class DeepLClient:
    def __init__(
        self,
        client: httpx.AsyncClient,
        api_key: str,
        concurrency: int = 4,
        timeout: float = 30.0,
    ) -> None:
        self.client = client
        self.api_key = api_key.strip()
        self.url = DEEPL_FREE_URL if self.api_key.endswith(":fx") else DEEPL_PRO_URL
        self.semaphore = asyncio.Semaphore(max(1, concurrency))
        self.timeout = max(1.0, timeout)

    async def translate_many(
        self,
        texts: list[str],
        source_lang: str = "AUTO",
    ) -> list[str | None]:
        if not texts:
            return []
        language = _source_language(source_lang)
        results: list[str | None] = ["" if not text else None for text in texts]
        indexed = [(index, text) for index, text in enumerate(texts) if text]
        batches = self._build_batches(indexed, language)
        translated_batches = await asyncio.gather(
            *(
                self._translate_batch([text for _index, text in batch], language)
                for batch in batches
            )
        )
        for batch, translations in zip(batches, translated_batches, strict=True):
            for (index, _text), translated in zip(batch, translations, strict=True):
                results[index] = translated
        return results

    def _build_batches(
        self,
        indexed_texts: list[tuple[int, str]],
        source_lang: str,
    ) -> list[list[tuple[int, str]]]:
        batches: list[list[tuple[int, str]]] = []
        current: list[tuple[int, str]] = []
        for item in indexed_texts:
            candidate = [*current, item]
            candidate_texts = [text for _index, text in candidate]
            if (
                len(candidate) <= DEEPL_MAX_TEXTS
                and self._payload_size(candidate_texts, source_lang) < DEEPL_MAX_REQUEST_BYTES
            ):
                current = candidate
                continue
            if not current:
                raise TranslationInputTooLargeError("单个翻译文本超过 DeepL 请求上限")
            batches.append(current)
            current = [item]
            if self._payload_size([item[1]], source_lang) >= DEEPL_MAX_REQUEST_BYTES:
                raise TranslationInputTooLargeError("单个翻译文本超过 DeepL 请求上限")
        if current:
            batches.append(current)
        return batches

    async def _translate_batch(self, texts: list[str], source_lang: str) -> list[str]:
        payload = self._payload(texts, source_lang)
        content = _encode_payload(payload)
        async with self.semaphore:
            response = await self._request(
                content,
                text_count=len(texts),
                total_chars=sum(len(text) for text in texts),
                source_lang=source_lang,
                sensitive_texts=texts,
            )
        try:
            try:
                data = response.json()
            except (json.JSONDecodeError, UnicodeDecodeError) as exc:
                raise TranslationProtocolError("DeepL 返回内容不是有效 JSON") from exc
            translations = data.get("translations") if isinstance(data, dict) else None
            if not isinstance(translations, list) or len(translations) != len(texts):
                raise TranslationProtocolError("DeepL 返回的翻译数量与请求不一致")
            output: list[str] = []
            for translation in translations:
                value = translation.get("text") if isinstance(translation, dict) else None
                if not isinstance(value, str):
                    raise TranslationProtocolError("DeepL 返回格式异常，缺少翻译文本")
                output.append(value)
        except TranslationProtocolError:
            _log_translation_protocol_failure(
                "deepl",
                response,
                operation="translate_batch",
                secrets=(self.api_key,),
                sensitive_texts=texts,
            )
            raise
        log_event(
            "deepl",
            "completed",
            operation="translate_batch",
            request_ref=response.extensions.get("comiclens_request_ref"),
            success_count=len(output),
            text_count=len(texts),
        )
        return output

    async def _request(
        self,
        content: bytes,
        *,
        text_count: int,
        total_chars: int,
        source_lang: str,
        sensitive_texts: Iterable[str],
    ) -> httpx.Response:
        if not self.api_key:
            raise ValueError("DeepL API Key 不能为空")
        request_ref = new_request_ref()
        endpoint = safe_endpoint(self.url, secrets=(self.api_key,))
        fields = {
            "operation": "translate_batch",
            "request_ref": request_ref,
            "auth": "api_key",
            "text_count": text_count,
            "total_chars": total_chars,
            "payload_bytes": len(content),
            "source_lang": source_lang,
            "target_lang": "ZH-HANS",
        }
        last_error: Exception | None = None
        total_started = time.monotonic()
        for attempt in range(3):
            log_event(
                "deepl",
                "request",
                **fields,
                method="POST",
                endpoint=endpoint,
                attempt=attempt + 1,
            )
            attempt_started = time.monotonic()
            try:
                response = await self.client.post(
                    self.url,
                    content=content,
                    headers={
                        "Authorization": f"DeepL-Auth-Key {self.api_key}",
                        "Content-Type": "application/json",
                    },
                    timeout=self.timeout,
                )
                response.extensions["comiclens_request_ref"] = request_ref
                response.extensions["comiclens_attempt"] = attempt + 1
                _log_translation_response(
                    "deepl",
                    response,
                    fields=fields,
                    endpoint=endpoint,
                    attempt=attempt + 1,
                    attempt_started=attempt_started,
                )
                if response.status_code >= 400:
                    _log_translation_error_detail(
                        "deepl",
                        response,
                        operation="translate_batch",
                        request_ref=request_ref,
                        secrets=(self.api_key,),
                        sensitive_texts=sensitive_texts,
                    )
                if (response.status_code == 429 or response.status_code >= 500) and attempt < 2:
                    delay = 0.2 * (2**attempt)
                    _log_translation_retry(
                        "deepl",
                        operation="translate_batch",
                        request_ref=request_ref,
                        attempt=attempt + 1,
                        delay=delay,
                        status=response.status_code,
                    )
                    await asyncio.sleep(delay)
                    continue
                if response.status_code in {401, 403}:
                    error = DeepLAuthenticationError("DeepL API Key 无效或没有访问权限")
                    _log_translation_failure(
                        "deepl",
                        operation="translate_batch",
                        request_ref=request_ref,
                        endpoint=endpoint,
                        error=error,
                        attempts=attempt + 1,
                        total_started=total_started,
                        status=response.status_code,
                    )
                    raise error
                if response.status_code == 456:
                    error = DeepLQuotaExceededError("DeepL API 配额已用尽")
                    _log_translation_failure(
                        "deepl",
                        operation="translate_batch",
                        request_ref=request_ref,
                        endpoint=endpoint,
                        error=error,
                        attempts=attempt + 1,
                        total_started=total_started,
                        status=response.status_code,
                    )
                    raise error
                if response.status_code == 429:
                    error = DeepLRateLimitError("DeepL API 请求过于频繁")
                    _log_translation_failure(
                        "deepl",
                        operation="translate_batch",
                        request_ref=request_ref,
                        endpoint=endpoint,
                        error=error,
                        attempts=attempt + 1,
                        total_started=total_started,
                        status=response.status_code,
                    )
                    raise error
                response.raise_for_status()
                return response
            except (httpx.TimeoutException, httpx.NetworkError) as exc:
                last_error = exc
                if attempt < 2:
                    delay = 0.2 * (2**attempt)
                    _log_translation_retry(
                        "deepl",
                        operation="translate_batch",
                        request_ref=request_ref,
                        attempt=attempt + 1,
                        delay=delay,
                        error=type(exc).__name__,
                    )
                    await asyncio.sleep(delay)
                    continue
                break
            except httpx.HTTPError as exc:
                _log_translation_failure(
                    "deepl",
                    operation="translate_batch",
                    request_ref=request_ref,
                    endpoint=endpoint,
                    error=exc,
                    attempts=attempt + 1,
                    total_started=total_started,
                )
                raise
        assert last_error is not None
        _log_translation_failure(
            "deepl",
            operation="translate_batch",
            request_ref=request_ref,
            endpoint=endpoint,
            error=last_error,
            attempts=3,
            total_started=total_started,
        )
        raise last_error

    @staticmethod
    def _payload(texts: list[str], source_lang: str) -> dict[str, object]:
        payload: dict[str, object] = {
            "text": texts,
            "target_lang": "ZH-HANS",
            "model_type": "quality_optimized",
        }
        if source_lang != "AUTO":
            payload["source_lang"] = source_lang
        return payload

    def _payload_size(self, texts: list[str], source_lang: str) -> int:
        return len(_encode_payload(self._payload(texts, source_lang)))


class DeepLXClient:
    def __init__(
        self,
        client: httpx.AsyncClient,
        url: str,
        concurrency: int = 4,
        timeout: float = 30.0,
    ) -> None:
        self.client = client
        self.url = url.rstrip("/")
        self.semaphore = asyncio.Semaphore(max(1, concurrency))
        self.timeout = max(1.0, timeout)

    async def translate_many(
        self,
        texts: list[str],
        source_lang: str = "AUTO",
    ) -> list[str | None]:
        language = _source_language(source_lang)
        deeplx_language = "auto" if language == "AUTO" else language
        return await asyncio.gather(*(self._translate_one(text, deeplx_language) for text in texts))

    async def _translate_one(self, text: str, source_lang: str) -> str | None:
        if not text:
            return ""
        payload = {
            "text": text,
            "source_lang": source_lang,
            "target_lang": "ZH",
        }
        async with self.semaphore:
            response = await self._request(
                payload,
                source_chars=len(text),
                source_lang=source_lang,
                sensitive_text=text,
            )
        try:
            try:
                response_payload = response.json()
            except (json.JSONDecodeError, UnicodeDecodeError) as exc:
                raise TranslationProtocolError("DeepLX 返回内容不是有效 JSON") from exc
            translated = self._extract_translation(response_payload)
            if translated is None:
                raise TranslationProtocolError("DeepLX 返回格式异常，缺少翻译文本")
        except TranslationProtocolError:
            _log_translation_protocol_failure(
                "deeplx",
                response,
                operation="translate_text",
                sensitive_texts=(text,),
            )
            raise
        log_event(
            "deeplx",
            "completed",
            operation="translate_text",
            request_ref=response.extensions.get("comiclens_request_ref"),
            success_count=1,
        )
        return translated

    async def _request(
        self,
        payload: dict[str, str],
        *,
        source_chars: int,
        source_lang: str,
        sensitive_text: str,
    ) -> httpx.Response:
        request_ref = new_request_ref()
        endpoint = safe_endpoint(self.url)
        payload_bytes = len(_encode_payload(payload))
        fields = {
            "operation": "translate_text",
            "request_ref": request_ref,
            "auth": "none",
            "source_chars": source_chars,
            "payload_bytes": payload_bytes,
            "source_lang": source_lang,
            "target_lang": "ZH",
        }
        last_error: Exception | None = None
        total_started = time.monotonic()
        for attempt in range(3):
            log_event(
                "deeplx",
                "request",
                **fields,
                method="POST",
                endpoint=endpoint,
                attempt=attempt + 1,
            )
            attempt_started = time.monotonic()
            try:
                response = await self.client.post(
                    self.url,
                    json=payload,
                    headers={"Content-Type": "application/json"},
                    timeout=self.timeout,
                )
                response.extensions["comiclens_request_ref"] = request_ref
                response.extensions["comiclens_attempt"] = attempt + 1
                _log_translation_response(
                    "deeplx",
                    response,
                    fields=fields,
                    endpoint=endpoint,
                    attempt=attempt + 1,
                    attempt_started=attempt_started,
                )
                if response.status_code >= 400:
                    _log_translation_error_detail(
                        "deeplx",
                        response,
                        operation="translate_text",
                        request_ref=request_ref,
                        sensitive_texts=(sensitive_text,),
                    )
                if (response.status_code == 429 or response.status_code >= 500) and attempt < 2:
                    delay = 0.2 * (2**attempt)
                    _log_translation_retry(
                        "deeplx",
                        operation="translate_text",
                        request_ref=request_ref,
                        attempt=attempt + 1,
                        delay=delay,
                        status=response.status_code,
                    )
                    await asyncio.sleep(delay)
                    continue
                response.raise_for_status()
                return response
            except (httpx.TimeoutException, httpx.NetworkError) as exc:
                last_error = exc
                if attempt < 2:
                    delay = 0.2 * (2**attempt)
                    _log_translation_retry(
                        "deeplx",
                        operation="translate_text",
                        request_ref=request_ref,
                        attempt=attempt + 1,
                        delay=delay,
                        error=type(exc).__name__,
                    )
                    await asyncio.sleep(delay)
                    continue
                break
            except httpx.HTTPError as exc:
                _log_translation_failure(
                    "deeplx",
                    operation="translate_text",
                    request_ref=request_ref,
                    endpoint=endpoint,
                    error=exc,
                    attempts=attempt + 1,
                    total_started=total_started,
                )
                raise
        assert last_error is not None
        _log_translation_failure(
            "deeplx",
            operation="translate_text",
            request_ref=request_ref,
            endpoint=endpoint,
            error=last_error,
            attempts=3,
            total_started=total_started,
        )
        raise last_error

    @staticmethod
    def _extract_translation(data: Any) -> str | None:
        if not isinstance(data, dict):
            return None
        if isinstance(data.get("data"), str):
            return data["data"]
        if isinstance(data.get("translation"), str):
            return data["translation"]
        translations = data.get("translations")
        if isinstance(translations, list) and translations:
            first = translations[0]
            if isinstance(first, str):
                return first
            if isinstance(first, dict):
                for key in ("text", "translation", "data"):
                    if isinstance(first.get(key), str):
                        return first[key]
        return None


def _source_language(source_lang: str) -> str:
    language = (source_lang or "AUTO").strip().upper()
    if language not in {"AUTO", "EN", "KO"}:
        raise ValueError("翻译源语言无效")
    return language


def _encode_payload(payload: dict[str, object]) -> bytes:
    return json.dumps(
        payload,
        ensure_ascii=False,
        allow_nan=False,
        separators=(",", ":"),
    ).encode("utf-8")


def _log_translation_response(
    service: str,
    response: httpx.Response,
    *,
    fields: dict[str, object],
    endpoint: str,
    attempt: int,
    attempt_started: float,
) -> None:
    content_type = response.headers.get("content-type", "").split(";", 1)[0]
    log_event(
        service,
        "response",
        **fields,
        status=response.status_code,
        duration_ms=round((time.monotonic() - attempt_started) * 1000),
        response_bytes=len(response.content),
        content_type=content_type or None,
        endpoint=endpoint,
        attempt=attempt,
    )


def _log_translation_retry(
    service: str,
    *,
    operation: str,
    request_ref: str,
    attempt: int,
    delay: float,
    status: int | None = None,
    error: str | None = None,
) -> None:
    log_event(
        service,
        "retry",
        level=logging.WARNING,
        operation=operation,
        request_ref=request_ref,
        status=status,
        attempt=attempt,
        next_attempt=attempt + 1,
        delay_ms=round(delay * 1000),
        error=error,
    )


def _log_translation_failure(
    service: str,
    *,
    operation: str,
    request_ref: object,
    endpoint: str | None,
    error: Exception,
    attempts: int,
    total_started: float | None = None,
    status: int | None = None,
) -> None:
    if status is None and isinstance(error, httpx.HTTPStatusError):
        status = error.response.status_code
    log_event(
        service,
        "failed",
        level=logging.ERROR,
        operation=operation,
        request_ref=request_ref,
        endpoint=endpoint,
        status=status,
        attempts=attempts,
        duration_ms=(
            round((time.monotonic() - total_started) * 1000) if total_started is not None else None
        ),
        error=type(error).__name__,
    )


def _log_translation_error_detail(
    service: str,
    response: httpx.Response,
    *,
    operation: str,
    request_ref: object,
    secrets: Iterable[str] = (),
    sensitive_texts: Iterable[str] = (),
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
        service,
        "error_detail",
        level=logging.DEBUG,
        operation=operation,
        request_ref=request_ref,
        status=response.status_code,
        excerpt=excerpt,
        truncated=truncated,
    )


def _log_translation_protocol_failure(
    service: str,
    response: httpx.Response,
    *,
    operation: str,
    secrets: Iterable[str] = (),
    sensitive_texts: Iterable[str] = (),
) -> None:
    request_ref = response.extensions.get("comiclens_request_ref")
    _log_translation_failure(
        service,
        operation=operation,
        request_ref=request_ref,
        endpoint=None,
        error=TranslationProtocolError(),
        attempts=int(response.extensions.get("comiclens_attempt", 1)),
        status=response.status_code,
    )
    _log_translation_error_detail(
        service,
        response,
        operation=operation,
        request_ref=request_ref,
        secrets=secrets,
        sensitive_texts=sensitive_texts,
    )
