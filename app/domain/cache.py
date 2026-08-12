from __future__ import annotations

from app.domain.comic import ComicModel


class CacheStats(ComicModel):
    used_bytes: int
    max_bytes: int
    bundle_count: int
    entry_count: int
    over_limit: bool
