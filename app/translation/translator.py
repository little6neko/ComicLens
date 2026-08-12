from __future__ import annotations

import asyncio
from typing import Any

import httpx


class DeepLXClient:
    def __init__(self, client: httpx.AsyncClient, url: str, concurrency: int = 4) -> None:
        self.client = client
        self.url = url.rstrip("/")
        self.semaphore = asyncio.Semaphore(max(1, concurrency))

    async def translate(self, text: str, source_lang: str = "EN") -> str | None:
        if not text:
            return ""
        payload = {
            "text": text,
            "source_lang": source_lang,
            "target_lang": "ZH",
        }
        async with self.semaphore:
            response = await self._request(payload)
        data = response.json()
        return self._extract_translation(data)

    async def _request(self, payload: dict[str, str]) -> httpx.Response:
        last_error: Exception | None = None
        for attempt in range(3):
            try:
                response = await self.client.post(
                    self.url,
                    json=payload,
                    headers={"Content-Type": "application/json"},
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
