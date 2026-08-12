from __future__ import annotations

import asyncio
import hashlib
import io
import os
import time
import uuid
from collections import defaultdict
from collections.abc import Awaitable, Callable
from contextlib import suppress
from dataclasses import dataclass
from pathlib import Path, PurePosixPath

from PIL import Image, UnidentifiedImageError

from app.domain.cache import CacheStats
from app.errors import AppError
from app.repositories.database import Database

READING_LEASE_SECONDS = 300


@dataclass(frozen=True, slots=True)
class CachedMedia:
    content: bytes
    media_type: str
    etag: str


class MediaCache:
    def __init__(self, root: Path, database: Database, max_bytes: int) -> None:
        self.root = root.resolve()
        self.database = database
        self.max_bytes = max_bytes
        self._locks: defaultdict[str, asyncio.Lock] = defaultdict(asyncio.Lock)
        self.root.mkdir(parents=True, exist_ok=True)
        self.reconcile()

    async def get_or_create(
        self,
        *,
        bundle_key: str,
        bundle_kind: str,
        comic_id: str,
        chapter_id: str | None,
        relative_path: str,
        entry_kind: str,
        loader: Callable[[], Awaitable[tuple[bytes, str]]],
        protect: bool,
    ) -> CachedMedia:
        existing = self._read_indexed(relative_path, protect=protect)
        if existing is not None:
            return existing

        async with self._locks[relative_path]:
            existing = self._read_indexed(relative_path, protect=protect)
            if existing is not None:
                return existing
            content, claimed_media_type = await loader()
            media_type = self._verify_image(content, claimed_media_type)
            checksum = hashlib.sha256(content).hexdigest()
            target = self._resolve_relative(relative_path)
            self._atomic_write(target, content)
            timestamp = self._timestamp()
            protected_until = timestamp + READING_LEASE_SECONDS if protect else 0
            try:
                with self.database.transaction() as connection:
                    connection.execute(
                        """
                        INSERT INTO cache_bundles(
                            bundle_key, kind, comic_id, chapter_id, byte_size,
                            accessed_at, protected_until, active_task
                        ) VALUES (?, ?, ?, ?, 0, ?, ?, 0)
                        ON CONFLICT(bundle_key) DO UPDATE SET
                            accessed_at = excluded.accessed_at,
                            protected_until = MAX(
                                cache_bundles.protected_until,
                                excluded.protected_until
                            )
                        """,
                        (
                            bundle_key,
                            bundle_kind,
                            comic_id,
                            chapter_id,
                            timestamp,
                            protected_until,
                        ),
                    )
                    previous_size_row = connection.execute(
                        "SELECT byte_size FROM cache_entries WHERE relative_path = ?",
                        (relative_path,),
                    ).fetchone()
                    previous_size = int(previous_size_row[0]) if previous_size_row else 0
                    connection.execute(
                        """
                        INSERT INTO cache_entries(
                            bundle_key, relative_path, entry_kind, byte_size,
                            checksum, created_at
                        ) VALUES (?, ?, ?, ?, ?, ?)
                        ON CONFLICT(relative_path) DO UPDATE SET
                            bundle_key = excluded.bundle_key,
                            entry_kind = excluded.entry_kind,
                            byte_size = excluded.byte_size,
                            checksum = excluded.checksum,
                            created_at = excluded.created_at
                        """,
                        (
                            bundle_key,
                            relative_path,
                            entry_kind,
                            len(content),
                            checksum,
                            timestamp,
                        ),
                    )
                    connection.execute(
                        """
                        UPDATE cache_bundles
                        SET byte_size = byte_size + ?, accessed_at = ?
                        WHERE bundle_key = ?
                        """,
                        (len(content) - previous_size, timestamp, bundle_key),
                    )
            except Exception:
                target.unlink(missing_ok=True)
                raise
            self.enforce_limit(exclude_bundle=bundle_key)
            return CachedMedia(content=content, media_type=media_type, etag=checksum)

    def stats(self) -> CacheStats:
        used = int(
            self.database.scalar("SELECT COALESCE(SUM(byte_size), 0) FROM cache_entries") or 0
        )
        bundles = int(self.database.scalar("SELECT COUNT(*) FROM cache_bundles") or 0)
        entries = int(self.database.scalar("SELECT COUNT(*) FROM cache_entries") or 0)
        return CacheStats(
            used_bytes=used,
            max_bytes=self.max_bytes,
            bundle_count=bundles,
            entry_count=entries,
            over_limit=used > self.max_bytes,
        )

    def lease_chapter(self, comic_id: str, chapter_id: str) -> None:
        self.database.execute(
            """
            UPDATE cache_bundles
            SET protected_until = MAX(protected_until, ?), accessed_at = ?
            WHERE kind = 'chapter' AND comic_id = ? AND chapter_id = ?
            """,
            (
                self._timestamp() + READING_LEASE_SECONDS,
                self._timestamp(),
                comic_id,
                chapter_id,
            ),
        )

    def remove_chapter(self, comic_id: str, chapter_id: str) -> bool:
        row = self.database.fetchone(
            """
            SELECT bundle_key, active_task, protected_until FROM cache_bundles
            WHERE kind = 'chapter' AND comic_id = ? AND chapter_id = ?
            """,
            (comic_id, chapter_id),
        )
        if row is None:
            return False
        if bool(row["active_task"]):
            raise AppError("CACHE_IN_USE", "该章节正在翻译，无法删除缓存", 409, True)
        if int(row["protected_until"]) > self._timestamp():
            raise AppError("CACHE_IN_USE", "该章节正在阅读，无法删除缓存", 409, True)
        self._remove_bundle(str(row["bundle_key"]))
        return True

    def clear(self) -> int:
        rows = self.database.fetchall(
            """
            SELECT bundle_key FROM cache_bundles
            WHERE active_task = 0 AND protected_until <= ?
            """,
            (self._timestamp(),),
        )
        removed = 0
        for row in rows:
            self._remove_bundle(str(row["bundle_key"]))
            removed += 1
        return removed

    def enforce_limit(self, *, exclude_bundle: str | None = None) -> None:
        while self.stats().used_bytes > self.max_bytes:
            timestamp = self._timestamp()
            parameters: list[object] = [timestamp]
            exclude_sql = ""
            if exclude_bundle is not None:
                exclude_sql = "AND bundle_key != ?"
                parameters.append(exclude_bundle)
            candidate = self.database.fetchone(
                f"""
                SELECT bundle_key FROM cache_bundles
                WHERE active_task = 0 AND protected_until <= ? {exclude_sql}
                ORDER BY accessed_at ASC, bundle_key ASC LIMIT 1
                """,
                tuple(parameters),
            )
            if candidate is None:
                return
            self._remove_bundle(str(candidate["bundle_key"]))

    def reconcile(self) -> None:
        for temporary in self.root.rglob("*.tmp"):
            with suppress(OSError):
                temporary.unlink()

        rows = self.database.fetchall(
            "SELECT entry_id, bundle_key, relative_path, byte_size FROM cache_entries"
        )
        affected_bundles: set[str] = set()
        indexed_paths: set[Path] = set()
        for row in rows:
            relative_path = str(row["relative_path"])
            try:
                path = self._resolve_relative(relative_path)
            except ValueError:
                path = self.root / "invalid-index-path"
            indexed_paths.add(path)
            if not path.is_file() or path.stat().st_size != int(row["byte_size"]):
                self.database.execute(
                    "DELETE FROM cache_entries WHERE entry_id = ?",
                    (int(row["entry_id"]),),
                )
                with suppress(OSError):
                    path.unlink(missing_ok=True)
                affected_bundles.add(str(row["bundle_key"]))

        for bundle_key in affected_bundles:
            self._recalculate_bundle(bundle_key)

        for path in self.root.rglob("*"):
            if path.is_file() and path not in indexed_paths:
                with suppress(OSError):
                    path.unlink()
        self._remove_empty_directories()

    def _read_indexed(self, relative_path: str, *, protect: bool) -> CachedMedia | None:
        row = self.database.fetchone(
            "SELECT bundle_key, byte_size, checksum FROM cache_entries WHERE relative_path = ?",
            (relative_path,),
        )
        if row is None:
            return None
        try:
            target = self._resolve_relative(relative_path)
            content = target.read_bytes()
        except (OSError, ValueError):
            content = b""
        checksum = hashlib.sha256(content).hexdigest() if content else ""
        if len(content) != int(row["byte_size"]) or checksum != str(row["checksum"]):
            self.database.execute(
                "DELETE FROM cache_entries WHERE relative_path = ?", (relative_path,)
            )
            self._recalculate_bundle(str(row["bundle_key"]))
            return None
        media_type = self._verify_image(content, "")
        timestamp = self._timestamp()
        protected_until = timestamp + READING_LEASE_SECONDS if protect else 0
        self.database.execute(
            """
            UPDATE cache_bundles SET accessed_at = ?,
                protected_until = MAX(protected_until, ?)
            WHERE bundle_key = ?
            """,
            (timestamp, protected_until, str(row["bundle_key"])),
        )
        return CachedMedia(content=content, media_type=media_type, etag=checksum)

    def _remove_bundle(self, bundle_key: str) -> None:
        bundle = self.database.fetchone(
            "SELECT kind, comic_id, chapter_id FROM cache_bundles WHERE bundle_key = ?",
            (bundle_key,),
        )
        if bundle is None:
            return
        rows = self.database.fetchall(
            "SELECT relative_path FROM cache_entries WHERE bundle_key = ?",
            (bundle_key,),
        )
        with self.database.transaction() as connection:
            connection.execute("DELETE FROM cache_bundles WHERE bundle_key = ?", (bundle_key,))
            if bundle["kind"] == "chapter" and bundle["chapter_id"] is not None:
                connection.execute(
                    """
                    DELETE FROM translation_generations
                    WHERE comic_id = ? AND chapter_id = ?
                    """,
                    (str(bundle["comic_id"]), str(bundle["chapter_id"])),
                )
        for row in rows:
            with suppress(OSError, ValueError):
                self._resolve_relative(str(row["relative_path"])).unlink(missing_ok=True)
        self._remove_empty_directories()

    def _recalculate_bundle(self, bundle_key: str) -> None:
        byte_size = int(
            self.database.scalar(
                "SELECT COALESCE(SUM(byte_size), 0) FROM cache_entries WHERE bundle_key = ?",
                (bundle_key,),
            )
            or 0
        )
        if byte_size == 0:
            self.database.execute("DELETE FROM cache_bundles WHERE bundle_key = ?", (bundle_key,))
        else:
            self.database.execute(
                "UPDATE cache_bundles SET byte_size = ? WHERE bundle_key = ?",
                (byte_size, bundle_key),
            )

    def _resolve_relative(self, relative_path: str) -> Path:
        pure_path = PurePosixPath(relative_path)
        if pure_path.is_absolute() or ".." in pure_path.parts:
            raise ValueError("cache path escapes root")
        path = self.root.joinpath(*pure_path.parts).resolve()
        if path != self.root and self.root not in path.parents:
            raise ValueError("cache path escapes root")
        return path

    @staticmethod
    def _verify_image(content: bytes, claimed_media_type: str) -> str:
        if not content:
            raise AppError("UPSTREAM_MEDIA_ERROR", "图片内容为空", 502, True)
        try:
            with Image.open(io.BytesIO(content)) as image:
                image.verify()
                image_format = (image.format or "").upper()
        except (UnidentifiedImageError, OSError, ValueError) as exc:
            raise AppError("UPSTREAM_MEDIA_ERROR", "图片内容损坏", 502, True) from exc
        media_types = {
            "AVIF": "image/avif",
            "GIF": "image/gif",
            "JPEG": "image/jpeg",
            "PNG": "image/png",
            "WEBP": "image/webp",
        }
        detected = media_types.get(image_format)
        if detected is None:
            raise AppError("UPSTREAM_MEDIA_ERROR", "图片格式不受支持", 502, True)
        if claimed_media_type.startswith("image/"):
            return detected
        return detected

    @staticmethod
    def _atomic_write(target: Path, content: bytes) -> None:
        target.parent.mkdir(parents=True, exist_ok=True)
        temporary = target.with_name(f".{target.name}.{uuid.uuid4().hex}.tmp")
        descriptor = os.open(temporary, os.O_WRONLY | os.O_CREAT | os.O_EXCL, 0o600)
        try:
            with os.fdopen(descriptor, "wb") as handle:
                handle.write(content)
                handle.flush()
                os.fsync(handle.fileno())
            os.replace(temporary, target)
        except Exception:
            with suppress(OSError):
                temporary.unlink()
            raise

    def _remove_empty_directories(self) -> None:
        directories = sorted(
            (path for path in self.root.rglob("*") if path.is_dir()),
            key=lambda path: len(path.parts),
            reverse=True,
        )
        for directory in directories:
            with suppress(OSError):
                directory.rmdir()

    @staticmethod
    def _timestamp() -> int:
        return int(time.time())
