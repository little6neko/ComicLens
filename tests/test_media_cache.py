from __future__ import annotations

import io
from pathlib import Path

import pytest
from PIL import Image

from app.cache.storage import MediaCache
from app.errors import AppError
from app.repositories.database import Database


def png_bytes(color: str = "white", size: tuple[int, int] = (2, 2)) -> bytes:
    buffer = io.BytesIO()
    Image.new("RGB", size, color).save(buffer, format="PNG")
    return buffer.getvalue()


@pytest.mark.asyncio
async def test_cache_fetches_once_and_survives_restart(tmp_path: Path) -> None:
    database = Database(tmp_path / "comiclens.db")
    calls = 0

    async def loader() -> tuple[bytes, str]:
        nonlocal calls
        calls += 1
        return png_bytes(), "image/png"

    first_cache = MediaCache(tmp_path / "cache", database, 1_000_000)
    first = await first_cache.get_or_create(
        bundle_key="cover:alpha",
        bundle_kind="cover",
        comic_id="alpha",
        chapter_id=None,
        relative_path="covers/alpha.img",
        entry_kind="cover",
        loader=loader,
        protect=False,
    )
    second = await first_cache.get_or_create(
        bundle_key="cover:alpha",
        bundle_kind="cover",
        comic_id="alpha",
        chapter_id=None,
        relative_path="covers/alpha.img",
        entry_kind="cover",
        loader=loader,
        protect=False,
    )
    restarted_cache = MediaCache(tmp_path / "cache", database, 1_000_000)
    restarted = await restarted_cache.get_or_create(
        bundle_key="cover:alpha",
        bundle_kind="cover",
        comic_id="alpha",
        chapter_id=None,
        relative_path="covers/alpha.img",
        entry_kind="cover",
        loader=loader,
        protect=False,
    )

    assert calls == 1
    assert first.content == second.content == restarted.content
    assert first.etag == restarted.etag
    assert restarted_cache.stats().bundle_count == 1
    database.close()


@pytest.mark.asyncio
async def test_cache_rejects_invalid_images_without_index_or_file(tmp_path: Path) -> None:
    database = Database(tmp_path / "comiclens.db")
    cache = MediaCache(tmp_path / "cache", database, 1_000_000)

    async def loader() -> tuple[bytes, str]:
        return b"not an image", "image/png"

    with pytest.raises(AppError) as error:
        await cache.get_or_create(
            bundle_key="cover:broken",
            bundle_kind="cover",
            comic_id="broken",
            chapter_id=None,
            relative_path="covers/broken.img",
            entry_kind="cover",
            loader=loader,
            protect=False,
        )

    assert error.value.code == "UPSTREAM_MEDIA_ERROR"
    assert cache.stats().entry_count == 0
    assert list((tmp_path / "cache").rglob("*")) == []
    database.close()


@pytest.mark.asyncio
async def test_lru_evicts_whole_old_bundle_but_protects_current_write(
    tmp_path: Path,
) -> None:
    database = Database(tmp_path / "comiclens.db")
    first_content = png_bytes("red", (8, 8))
    second_content = png_bytes("blue", (8, 8))
    max_bytes = max(len(first_content), len(second_content)) + 8
    cache = MediaCache(tmp_path / "cache", database, max_bytes)

    async def first_loader() -> tuple[bytes, str]:
        return first_content, "image/png"

    async def second_loader() -> tuple[bytes, str]:
        return second_content, "image/png"

    await cache.get_or_create(
        bundle_key="cover:first",
        bundle_kind="cover",
        comic_id="first",
        chapter_id=None,
        relative_path="covers/first.img",
        entry_kind="cover",
        loader=first_loader,
        protect=False,
    )
    database.execute("UPDATE cache_bundles SET accessed_at = 1 WHERE bundle_key = 'cover:first'")
    await cache.get_or_create(
        bundle_key="cover:second",
        bundle_kind="cover",
        comic_id="second",
        chapter_id=None,
        relative_path="covers/second.img",
        entry_kind="cover",
        loader=second_loader,
        protect=False,
    )

    assert not (tmp_path / "cache/covers/first.img").exists()
    assert (tmp_path / "cache/covers/second.img").is_file()
    assert cache.stats().bundle_count == 1
    assert cache.stats().over_limit is False
    database.close()


@pytest.mark.asyncio
async def test_reading_lease_allows_temporary_overage_and_blocks_manual_delete(
    tmp_path: Path,
) -> None:
    database = Database(tmp_path / "comiclens.db")
    content = png_bytes("green", (8, 8))
    cache = MediaCache(tmp_path / "cache", database, max_bytes=1)

    async def loader() -> tuple[bytes, str]:
        return content, "image/png"

    await cache.get_or_create(
        bundle_key="chapter:current",
        bundle_kind="chapter",
        comic_id="alpha",
        chapter_id="chapter-1",
        relative_path="chapters/current/originals/00000.img",
        entry_kind="original",
        loader=loader,
        protect=True,
    )

    assert cache.stats().over_limit is True
    with pytest.raises(AppError) as error:
        cache.remove_chapter("alpha", "chapter-1")
    assert error.value.code == "CACHE_IN_USE"

    database.execute(
        "UPDATE cache_bundles SET protected_until = 0 WHERE bundle_key = 'chapter:current'"
    )
    cache.enforce_limit()
    assert cache.stats().used_bytes == 0
    database.close()


@pytest.mark.asyncio
async def test_reconcile_removes_orphans_and_refetches_corrupt_indexed_file(
    tmp_path: Path,
) -> None:
    database = Database(tmp_path / "comiclens.db")
    cache_root = tmp_path / "cache"
    cache = MediaCache(cache_root, database, 1_000_000)
    calls = 0

    async def loader() -> tuple[bytes, str]:
        nonlocal calls
        calls += 1
        return png_bytes(), "image/png"

    await cache.get_or_create(
        bundle_key="cover:alpha",
        bundle_kind="cover",
        comic_id="alpha",
        chapter_id=None,
        relative_path="covers/alpha.img",
        entry_kind="cover",
        loader=loader,
        protect=False,
    )
    indexed = cache_root / "covers/alpha.img"
    indexed.write_bytes(b"corrupt")
    orphan = cache_root / "orphan.img"
    orphan.write_bytes(png_bytes())

    restarted = MediaCache(cache_root, database, 1_000_000)
    assert not indexed.exists()
    assert not orphan.exists()
    assert restarted.stats().entry_count == 0

    await restarted.get_or_create(
        bundle_key="cover:alpha",
        bundle_kind="cover",
        comic_id="alpha",
        chapter_id=None,
        relative_path="covers/alpha.img",
        entry_kind="cover",
        loader=loader,
        protect=False,
    )
    assert calls == 2
    database.close()
