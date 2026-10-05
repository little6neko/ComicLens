from __future__ import annotations

import asyncio
import threading
from pathlib import Path

import pytest
from fastapi.testclient import TestClient

from app.cache.keys import chapter_bundle_key
from app.cache.storage import MediaCache
from app.repositories.database import Database


def test_settings_save_does_not_wait_for_cache_cleanup(
    client: TestClient, monkeypatch: pytest.MonkeyPatch
) -> None:
    cache = client.app.state.media_cache
    started = threading.Event()
    release = threading.Event()
    calls = []

    def slow_cleanup(*, exclude_bundle=None):
        calls.append(cache.max_bytes)
        started.set()
        assert release.wait(5)

    monkeypatch.setattr(cache, "_enforce_limit", slow_cleanup)
    try:
        response = client.patch("/api/settings", json={"cacheMaxMb": 1024})
        assert response.status_code == 200
        assert started.wait(2)
        assert client.get("/health").status_code == 200
        assert client.get("/api/system/cache").json()["cleanupStatus"] == "running"
        assert client.patch("/api/settings", json={"cacheMaxMb": 2048}).status_code == 200
        assert len(calls) == 1  # A second save must not spawn a concurrent worker.
    finally:
        release.set()
    client.portal.call(cache.wait_for_cleanup)
    assert calls[-1] == 2048 * 1024 * 1024
    assert client.get("/api/system/cache").json()["cleanupStatus"] == "idle"


@pytest.mark.asyncio
async def test_cleanup_failure_is_reported_and_can_be_retried(
    tmp_path: Path, monkeypatch: pytest.MonkeyPatch
) -> None:
    database = Database(tmp_path / "cache.db")
    cache = MediaCache(tmp_path / "cache", database, 100)
    enforce = cache._enforce_limit

    def fail():
        raise OSError("test disk failure")

    monkeypatch.setattr(cache, "_enforce_limit", fail)
    cache.schedule_limit_enforcement()
    await cache.wait_for_cleanup()
    assert cache.stats().cleanup_status == "failed"
    assert cache.stats().cleanup_error
    monkeypatch.setattr(cache, "_enforce_limit", enforce)
    cache.schedule_limit_enforcement()
    await cache.wait_for_cleanup()
    assert cache.stats().cleanup_status == "idle"
    assert cache.stats().cleanup_error is None
    database.close()


@pytest.mark.asyncio
async def test_background_eviction_only_prunes_affected_directories(
    tmp_path: Path, monkeypatch: pytest.MonkeyPatch
) -> None:
    database = Database(tmp_path / "cache.db")
    cache = MediaCache(tmp_path / "cache", database, 1000)
    for name in ("old", "reading", "active"):
        cache.put_bytes(
            bundle_key=chapter_bundle_key(name, "1"),
            bundle_kind="chapter",
            comic_id=name,
            chapter_id="1",
            relative_path=f"chapters/{name}/1/originals/a.bin",
            entry_kind="original",
            content=b"x" * 100,
        )
    cache.lease_chapter("reading", "1")
    cache.set_chapter_active("active", "1", True)
    unrelated = cache.root / "unrelated/empty"
    unrelated.mkdir(parents=True)

    def no_full_scan(*args, **kwargs):
        pytest.fail("eviction must not scan the full cache tree")

    monkeypatch.setattr(Path, "rglob", no_full_scan)
    cache.max_bytes = 1
    cache.schedule_limit_enforcement()
    await asyncio.wait_for(cache.wait_for_cleanup(), 3)
    assert not (cache.root / "chapters/old").exists()
    assert (cache.root / "chapters/reading/1/originals/a.bin").is_file()
    assert (cache.root / "chapters/active/1/originals/a.bin").is_file()
    assert unrelated.is_dir()
    assert cache.stats().over_limit
    assert cache.stats().cleanup_status == "idle"
    database.close()


@pytest.mark.asyncio
async def test_failed_file_removal_keeps_index_for_retry(
    tmp_path: Path, monkeypatch: pytest.MonkeyPatch
) -> None:
    database = Database(tmp_path / "cache.db")
    cache = MediaCache(tmp_path / "cache", database, 1000)
    cache.put_bytes(
        bundle_key="cover:a",
        bundle_kind="cover",
        comic_id="a",
        chapter_id=None,
        relative_path="covers/a.bin",
        entry_kind="cover",
        content=b"x" * 100,
    )
    unlink = Path.unlink

    def fail(path, *args, **kwargs):
        raise PermissionError("test: cannot remove cache file")

    monkeypatch.setattr(Path, "unlink", fail)
    cache.max_bytes = 1
    cache.schedule_limit_enforcement()
    await cache.wait_for_cleanup()
    assert cache.stats().cleanup_status == "failed"
    assert cache.stats().used_bytes == 100
    assert (cache.root / "covers/a.bin").is_file()
    monkeypatch.setattr(Path, "unlink", unlink)
    cache.schedule_limit_enforcement()
    await cache.wait_for_cleanup()
    assert cache.stats().cleanup_status == "idle"
    assert cache.stats().used_bytes == 0
    assert not (cache.root / "covers").exists()
    database.close()


@pytest.mark.asyncio
async def test_cleanup_rechecks_reading_protection_after_selecting_candidate(
    tmp_path: Path, monkeypatch: pytest.MonkeyPatch
) -> None:
    database = Database(tmp_path / "cache.db")
    cache = MediaCache(tmp_path / "cache", database, 1000)
    cache.put_bytes(
        bundle_key=chapter_bundle_key("a", "1"),
        bundle_kind="chapter",
        comic_id="a",
        chapter_id="1",
        relative_path="chapters/a/1/a.bin",
        entry_kind="original",
        content=b"x" * 100,
    )
    remove = cache._remove_bundle

    def start_reading_before_remove(bundle_key):
        cache.lease_chapter("a", "1")
        return remove(bundle_key)

    monkeypatch.setattr(cache, "_remove_bundle", start_reading_before_remove)
    cache.max_bytes = 1
    cache.schedule_limit_enforcement()
    await asyncio.wait_for(cache.wait_for_cleanup(), 3)
    assert cache.stats().used_bytes == 100
    assert (cache.root / "chapters/a/1/a.bin").is_file()
    database.close()
