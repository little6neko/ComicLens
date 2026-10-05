from __future__ import annotations

from typing import Literal

from app.domain.comic import ComicModel


class CacheStats(ComicModel):
    used_bytes: int
    max_bytes: int
    bundle_count: int
    entry_count: int
    over_limit: bool
    cleanup_status: Literal["idle", "running", "failed"] = "idle"
    cleanup_error: str | None = None
