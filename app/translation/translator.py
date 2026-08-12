from __future__ import annotations

import asyncio
import json
from typing import Any

import httpx

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
            response = await self._request(content)
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
        return output

    async def _request(self, content: bytes) -> httpx.Response:
        if not self.api_key:
            raise ValueError("DeepL API Key 不能为空")
        last_error: Exception | None = None
        for attempt in range(3):
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
                if (response.status_code == 429 or response.status_code >= 500) and attempt < 2:
                    await asyncio.sleep(0.2 * (2**attempt))
                    continue
                if response.status_code in {401, 403}:
                    raise DeepLAuthenticationError("DeepL API Key 无效或没有访问权限")
                if response.status_code == 456:
                    raise DeepLQuotaExceededError("DeepL API 配额已用尽")
                if response.status_code == 429:
                    raise DeepLRateLimitError("DeepL API 请求过于频繁")
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

    @staticmethod
    def _payload(texts: list[str], source_lang: str) -> dict[str, object]:
        payload: dict[str, object] = {
            "text": texts,
            "target_lang": "ZH-HANS",
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
            response = await self._request(payload)
        try:
            response_payload = response.json()
        except (json.JSONDecodeError, UnicodeDecodeError) as exc:
            raise TranslationProtocolError("DeepLX 返回内容不是有效 JSON") from exc
        translated = self._extract_translation(response_payload)
        if translated is None:
            raise TranslationProtocolError("DeepLX 返回格式异常，缺少翻译文本")
        return translated

    async def _request(self, payload: dict[str, str]) -> httpx.Response:
        last_error: Exception | None = None
        for attempt in range(3):
            try:
                response = await self.client.post(
                    self.url,
                    json=payload,
                    headers={"Content-Type": "application/json"},
                    timeout=self.timeout,
                )
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
