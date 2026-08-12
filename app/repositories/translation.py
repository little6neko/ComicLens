from __future__ import annotations

import json
import sqlite3
import time
import uuid
from typing import Any

from app.domain.translation import (
    TranslationError,
    TranslationPageState,
    TranslationTaskState,
)
from app.repositories.database import Database

ACTIVE_TASK_STATUSES = ("queued", "running", "stopping_after_page")
TERMINAL_TASK_STATUSES = ("completed", "completed_with_errors", "failed")


class TranslationRepository:
    def __init__(self, database: Database) -> None:
        self.database = database

    def recover_interrupted(self) -> int:
        timestamp = self._timestamp()
        with self.database.transaction() as connection:
            cursor = connection.execute(
                """
                UPDATE translation_generations
                SET status = 'paused', stop_requested = 0,
                    current_page_index = NULL, updated_at = ?
                WHERE status IN ('queued', 'running', 'stopping_after_page')
                """,
                (timestamp,),
            )
            connection.execute(
                """
                UPDATE translation_pages SET status = 'pending', updated_at = ?
                WHERE status IN (
                    'downloading', 'ocr', 'translating', 'rendering'
                )
                """,
                (timestamp,),
            )
            connection.execute("UPDATE cache_bundles SET active_task = 0")
            return cursor.rowcount

    def recover_invalid_checkpoints(self) -> int:
        indexed_paths = {
            str(row["relative_path"])
            for row in self.database.fetchall("SELECT relative_path FROM cache_entries")
        }
        repaired = 0
        affected_generations: set[str] = set()
        rows = self.database.fetchall("SELECT * FROM translation_pages")
        for row in rows:
            generation_id = str(row["generation_id"])
            page_index = int(row["page_index"])
            status = str(row["status"])
            original_ok = bool(row["original_path"] in indexed_paths)
            ocr_ok = bool(row["ocr_path"] in indexed_paths and row["blocks_path"] in indexed_paths)
            translations_ok = bool(row["translations_path"] in indexed_paths)
            translated_ok = bool(row["translated_path"] in indexed_paths)

            clear_columns: list[str] = []
            rollback_stage: str | None = None
            if row["original_path"] and not original_ok:
                clear_columns = [
                    "original_path",
                    "ocr_path",
                    "blocks_path",
                    "translations_path",
                    "translated_path",
                ]
                rollback_stage = "downloading"
            elif (row["ocr_path"] or row["blocks_path"]) and not ocr_ok:
                clear_columns = [
                    "ocr_path",
                    "blocks_path",
                    "translations_path",
                    "translated_path",
                ]
                rollback_stage = "ocr"
            elif row["translations_path"] and not translations_ok:
                clear_columns = ["translations_path", "translated_path"]
                rollback_stage = "translating"
            elif row["translated_path"] and not translated_ok:
                clear_columns = ["translated_path"]
                rollback_stage = "rendering"

            if rollback_stage is None:
                continue
            assignments = [f"{column} = NULL" for column in clear_columns]
            assignments.extend(
                [
                    "translated_version = NULL",
                    "display_parts_json = '[]'",
                    "updated_at = ?",
                ]
            )
            parameters: list[object] = [self._timestamp()]
            if status == "failed":
                assignments.append("error_stage = ?")
                parameters.append(rollback_stage)
            else:
                assignments.extend(
                    [
                        "status = 'pending'",
                        "error_stage = NULL",
                        "error_code = NULL",
                        "error_summary = NULL",
                    ]
                )
            parameters.extend([generation_id, page_index])
            self.database.execute(
                f"""
                UPDATE translation_pages SET {", ".join(assignments)}
                WHERE generation_id = ? AND page_index = ?
                """,
                tuple(parameters),
            )
            self.database.execute(
                """
                DELETE FROM active_translation_pages
                WHERE generation_id = ? AND page_index = ?
                """,
                (generation_id, page_index),
            )
            repaired += 1
            affected_generations.add(generation_id)

        invalid_active = self.database.fetchall(
            """
            SELECT generation_id, page_index FROM active_translation_pages
            WHERE translated_path NOT IN (
                SELECT relative_path FROM cache_entries
            )
            """
        )
        for row in invalid_active:
            generation_id = str(row["generation_id"])
            page_index = int(row["page_index"])
            self.database.execute(
                """
                DELETE FROM active_translation_pages
                WHERE generation_id = ? AND page_index = ?
                """,
                (generation_id, page_index),
            )
            self.database.execute(
                """
                UPDATE translation_pages SET status = 'pending',
                    translated_path = NULL, translated_version = NULL,
                    display_parts_json = '[]', updated_at = ?
                WHERE generation_id = ? AND page_index = ?
                  AND status = 'completed'
                """,
                (self._timestamp(), generation_id, page_index),
            )
            repaired += 1
            affected_generations.add(generation_id)

        for generation_id in affected_generations:
            self.refresh_counts(generation_id)
            pending = int(
                self.database.scalar(
                    """
                    SELECT COUNT(*) FROM translation_pages
                    WHERE generation_id = ? AND status = 'pending'
                    """,
                    (generation_id,),
                )
                or 0
            )
            if pending:
                self.set_generation_status(generation_id, "paused", stop_requested=False)
        return repaired

    def create_generation(
        self,
        comic_id: str,
        chapter_id: str,
        *,
        semantic_fingerprint: str,
        semantic_settings: dict[str, object],
        page_indexes: list[int],
        kind: str,
    ) -> str:
        generation_id = uuid.uuid4().hex
        timestamp = self._timestamp()
        with self.database.transaction() as connection:
            connection.execute(
                """
                INSERT INTO translation_generations(
                    generation_id, comic_id, chapter_id,
                    semantic_fingerprint, semantic_settings_json,
                    status, stop_requested, total_pages, completed_pages,
                    failed_pages, created_at, updated_at, kind
                ) VALUES (?, ?, ?, ?, ?, 'queued', 0, ?, 0, 0, ?, ?, ?)
                """,
                (
                    generation_id,
                    comic_id,
                    chapter_id,
                    semantic_fingerprint,
                    json.dumps(
                        semantic_settings,
                        ensure_ascii=False,
                        separators=(",", ":"),
                        sort_keys=True,
                    ),
                    len(page_indexes),
                    timestamp,
                    timestamp,
                    kind,
                ),
            )
            connection.executemany(
                """
                INSERT INTO translation_pages(
                    generation_id, page_index, status, updated_at
                ) VALUES (?, ?, 'pending', ?)
                """,
                [(generation_id, page_index, timestamp) for page_index in page_indexes],
            )
        return generation_id

    def latest_generation(self, comic_id: str, chapter_id: str) -> sqlite3.Row | None:
        return self.database.fetchone(
            """
            SELECT * FROM translation_generations
            WHERE comic_id = ? AND chapter_id = ?
            ORDER BY created_at DESC, generation_id DESC LIMIT 1
            """,
            (comic_id, chapter_id),
        )

    def generation(self, generation_id: str) -> sqlite3.Row | None:
        return self.database.fetchone(
            "SELECT * FROM translation_generations WHERE generation_id = ?",
            (generation_id,),
        )

    def next_queued_generation(self, comic_id: str, chapter_id: str) -> sqlite3.Row | None:
        return self.database.fetchone(
            """
            SELECT * FROM translation_generations
            WHERE comic_id = ? AND chapter_id = ? AND status = 'queued'
            ORDER BY created_at ASC, generation_id ASC LIMIT 1
            """,
            (comic_id, chapter_id),
        )

    def matching_generation(
        self,
        comic_id: str,
        chapter_id: str,
        semantic_fingerprint: str,
    ) -> sqlite3.Row | None:
        return self.database.fetchone(
            """
            SELECT * FROM translation_generations
            WHERE comic_id = ? AND chapter_id = ?
              AND semantic_fingerprint = ? AND kind = 'normal'
            ORDER BY created_at DESC, generation_id DESC LIMIT 1
            """,
            (comic_id, chapter_id, semantic_fingerprint),
        )

    def active_generation(self, comic_id: str, chapter_id: str) -> sqlite3.Row | None:
        return self.database.fetchone(
            """
            SELECT * FROM translation_generations
            WHERE comic_id = ? AND chapter_id = ?
              AND status IN ('queued', 'running', 'stopping_after_page')
            ORDER BY created_at ASC, generation_id ASC LIMIT 1
            """,
            (comic_id, chapter_id),
        )

    def set_generation_status(
        self,
        generation_id: str,
        status: str,
        *,
        stop_requested: bool | None = None,
        current_page_index: int | None = None,
    ) -> None:
        fields = ["status = ?", "current_page_index = ?", "updated_at = ?"]
        parameters: list[object] = [
            status,
            current_page_index,
            self._timestamp(),
        ]
        if stop_requested is not None:
            fields.append("stop_requested = ?")
            parameters.append(int(stop_requested))
        parameters.append(generation_id)
        self.database.execute(
            f"UPDATE translation_generations SET {', '.join(fields)} WHERE generation_id = ?",
            tuple(parameters),
        )

    def request_stop(self, generation_id: str) -> str | None:
        row = self.generation(generation_id)
        if row is None:
            return None
        status = str(row["status"])
        if status == "queued":
            self.set_generation_status(generation_id, "paused", stop_requested=False)
            return "paused"
        if status == "running":
            self.set_generation_status(
                generation_id,
                "stopping_after_page",
                stop_requested=True,
                current_page_index=(
                    int(row["current_page_index"])
                    if row["current_page_index"] is not None
                    else None
                ),
            )
            return "stopping_after_page"
        return status

    def resume(self, generation_id: str) -> None:
        self.set_generation_status(generation_id, "queued", stop_requested=False)

    def page(self, generation_id: str, page_index: int) -> sqlite3.Row | None:
        return self.database.fetchone(
            """
            SELECT * FROM translation_pages
            WHERE generation_id = ? AND page_index = ?
            """,
            (generation_id, page_index),
        )

    def pending_pages(self, generation_id: str) -> list[sqlite3.Row]:
        return self.database.fetchall(
            """
            SELECT * FROM translation_pages
            WHERE generation_id = ? AND status = 'pending'
            ORDER BY page_index ASC
            """,
            (generation_id,),
        )

    def set_page_stage(
        self,
        generation_id: str,
        page_index: int,
        status: str,
        *,
        increment_attempts: bool = False,
        paths: dict[str, str | None] | None = None,
    ) -> None:
        fields = ["status = ?", "updated_at = ?"]
        parameters: list[object] = [status, self._timestamp()]
        if increment_attempts:
            fields.append("attempts = attempts + 1")
        for key, value in (paths or {}).items():
            if key not in {
                "original_path",
                "ocr_path",
                "blocks_path",
                "translations_path",
                "translated_path",
            }:
                raise ValueError("unsupported translation checkpoint column")
            fields.append(f"{key} = ?")
            parameters.append(value)
        fields.extend(["error_stage = NULL", "error_code = NULL", "error_summary = NULL"])
        parameters.extend([generation_id, page_index])
        self.database.execute(
            f"""
            UPDATE translation_pages SET {", ".join(fields)}
            WHERE generation_id = ? AND page_index = ?
            """,
            tuple(parameters),
        )

    def complete_page(
        self,
        generation_id: str,
        comic_id: str,
        chapter_id: str,
        page_index: int,
        *,
        translated_path: str,
        translated_version: str,
        width: int,
        height: int,
        display_parts: list[str],
    ) -> None:
        timestamp = self._timestamp()
        with self.database.transaction() as connection:
            connection.execute(
                """
                UPDATE translation_pages SET
                    status = 'completed', translated_path = ?,
                    translated_version = ?, width = ?, height = ?,
                    display_parts_json = ?, error_stage = NULL,
                    error_code = NULL, error_summary = NULL, updated_at = ?
                WHERE generation_id = ? AND page_index = ?
                """,
                (
                    translated_path,
                    translated_version,
                    width,
                    height,
                    json.dumps(display_parts, separators=(",", ":")),
                    timestamp,
                    generation_id,
                    page_index,
                ),
            )
            connection.execute(
                """
                INSERT INTO active_translation_pages(
                    comic_id, chapter_id, page_index, generation_id,
                    translated_path, translated_version, updated_at
                ) VALUES (?, ?, ?, ?, ?, ?, ?)
                ON CONFLICT(comic_id, chapter_id, page_index) DO UPDATE SET
                    generation_id = excluded.generation_id,
                    translated_path = excluded.translated_path,
                    translated_version = excluded.translated_version,
                    updated_at = excluded.updated_at
                """,
                (
                    comic_id,
                    chapter_id,
                    page_index,
                    generation_id,
                    translated_path,
                    translated_version,
                    timestamp,
                ),
            )
        self.refresh_counts(generation_id)

    def fail_page(
        self,
        generation_id: str,
        page_index: int,
        *,
        stage: str,
        code: str,
        summary: str,
    ) -> None:
        self.database.execute(
            """
            UPDATE translation_pages SET status = 'failed',
                error_stage = ?, error_code = ?, error_summary = ?, updated_at = ?
            WHERE generation_id = ? AND page_index = ?
            """,
            (stage, code, summary[:500], self._timestamp(), generation_id, page_index),
        )
        self.refresh_counts(generation_id)

    def prepare_retry(
        self,
        generation_id: str,
        page_index: int,
        *,
        clear_columns: list[str],
    ) -> list[str]:
        row = self.page(generation_id, page_index)
        if row is None:
            return []
        allowed = {
            "original_path",
            "ocr_path",
            "blocks_path",
            "translations_path",
            "translated_path",
        }
        columns = [column for column in clear_columns if column in allowed]
        paths = [str(row[column]) for column in columns if row[column]]
        assignments = [f"{column} = NULL" for column in columns]
        assignments.extend(
            [
                "status = 'pending'",
                "translated_version = NULL",
                "display_parts_json = '[]'",
                "error_stage = NULL",
                "error_code = NULL",
                "error_summary = NULL",
                "updated_at = ?",
            ]
        )
        self.database.execute(
            f"""
            UPDATE translation_pages SET {", ".join(assignments)}
            WHERE generation_id = ? AND page_index = ?
            """,
            (self._timestamp(), generation_id, page_index),
        )
        self.refresh_counts(generation_id)
        return paths

    def refresh_counts(self, generation_id: str) -> None:
        row = self.database.fetchone(
            """
            SELECT
                SUM(CASE WHEN status = 'completed' THEN 1 ELSE 0 END) completed,
                SUM(CASE WHEN status = 'failed' THEN 1 ELSE 0 END) failed
            FROM translation_pages WHERE generation_id = ?
            """,
            (generation_id,),
        )
        self.database.execute(
            """
            UPDATE translation_generations
            SET completed_pages = ?, failed_pages = ?, updated_at = ?
            WHERE generation_id = ?
            """,
            (
                int(row["completed"] or 0) if row else 0,
                int(row["failed"] or 0) if row else 0,
                self._timestamp(),
                generation_id,
            ),
        )

    def task_state(
        self, comic_id: str, chapter_id: str, generation_id: str | None = None
    ) -> TranslationTaskState:
        generation = (
            self.generation(generation_id)
            if generation_id
            else self.latest_generation(comic_id, chapter_id)
        )
        if generation is None:
            return TranslationTaskState(comic_id=comic_id, chapter_id=chapter_id)
        pages = self._page_states(comic_id, chapter_id, str(generation["generation_id"]))
        return TranslationTaskState(
            comic_id=comic_id,
            chapter_id=chapter_id,
            generation_id=str(generation["generation_id"]),
            kind=str(generation["kind"]),
            status=str(generation["status"]),
            stop_requested=bool(generation["stop_requested"]),
            current_page_index=(
                int(generation["current_page_index"])
                if generation["current_page_index"] is not None
                else None
            ),
            total_pages=int(generation["total_pages"]),
            completed_pages=int(generation["completed_pages"]),
            failed_pages=int(generation["failed_pages"]),
            pages=pages,
        )

    def active_page(self, comic_id: str, chapter_id: str, page_index: int) -> sqlite3.Row | None:
        return self.database.fetchone(
            """
            SELECT active.*, pages.width, pages.height, pages.display_parts_json
            FROM active_translation_pages active
            JOIN translation_pages pages
              ON pages.generation_id = active.generation_id
             AND pages.page_index = active.page_index
            WHERE active.comic_id = ? AND active.chapter_id = ?
              AND active.page_index = ?
            """,
            (comic_id, chapter_id, page_index),
        )

    def _page_states(
        self, comic_id: str, chapter_id: str, generation_id: str
    ) -> list[TranslationPageState]:
        rows = self.database.fetchall(
            """
            SELECT pages.*, active.translated_path active_path,
                active.translated_version active_version
            FROM translation_pages pages
            LEFT JOIN active_translation_pages active
              ON active.comic_id = ? AND active.chapter_id = ?
             AND active.page_index = pages.page_index
            WHERE pages.generation_id = ? ORDER BY pages.page_index
            """,
            (comic_id, chapter_id, generation_id),
        )
        return [
            TranslationPageState(
                page_index=int(row["page_index"]),
                status=str(row["status"]),
                translated_url=(
                    self.translated_url(
                        comic_id,
                        chapter_id,
                        int(row["page_index"]),
                        str(row["active_version"]),
                    )
                    if row["active_path"] and row["active_version"]
                    else None
                ),
                translated_version=(str(row["active_version"]) if row["active_version"] else None),
                width=int(row["width"]) if row["width"] is not None else None,
                height=int(row["height"]) if row["height"] is not None else None,
                attempts=int(row["attempts"]),
                error=(
                    TranslationError(
                        stage=str(row["error_stage"]),
                        code=str(row["error_code"]),
                        message=str(row["error_summary"]),
                    )
                    if row["error_code"]
                    else None
                ),
            )
            for row in rows
        ]

    @staticmethod
    def translated_url(
        comic_id: str,
        chapter_id: str,
        page_index: int,
        version: str,
    ) -> str:
        return (
            f"/api/media/comics/{comic_id}/chapters/{chapter_id}/pages/"
            f"{page_index}/translated?v={version}"
        )

    @staticmethod
    def decode_semantic_settings(row: sqlite3.Row) -> dict[str, Any]:
        value = json.loads(str(row["semantic_settings_json"]))
        return value if isinstance(value, dict) else {}

    @staticmethod
    def _timestamp() -> int:
        return time.time_ns() // 1_000_000
