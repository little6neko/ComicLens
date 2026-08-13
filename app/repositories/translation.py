from __future__ import annotations

import json
import sqlite3
import time
import uuid
from typing import Any

from app.domain.translation import (
    BackgroundTranslationTask,
    CurrentTranslationSegment,
    TranslationError,
    TranslationLayer,
    TranslationPageState,
    TranslationSegmentState,
    TranslationTaskState,
)
from app.repositories.database import Database

ACTIVE_TASK_STATUSES = (
    "preparing",
    "queued",
    "running",
    "stopping_after_page",
    "stopping_after_segment",
)
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
                SET status = CASE
                        WHEN (
                             semantic_settings_json LIKE
                             '%"pipelineVersion":"progressive-segment-v1"%'
                             OR semantic_settings_json LIKE
                             '%"pipelineVersion":"progressive-segment-v2"%'
                        )
                             AND status != 'stopping_after_segment'
                            THEN 'queued'
                        ELSE 'paused'
                    END,
                    stop_requested = 0, current_page_index = NULL,
                    current_segment_index = NULL, updated_at = ?
                WHERE status IN (
                    'preparing', 'queued', 'running', 'stopping_after_page',
                    'stopping_after_segment'
                )
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
            connection.execute(
                """
                UPDATE translation_segments SET status = 'pending', updated_at = ?
                WHERE status IN ('ocr', 'translating', 'rendering')
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
        rows = self.database.fetchall(
            """
            SELECT pages.* FROM translation_pages pages
            JOIN translation_generations generations
              ON generations.generation_id = pages.generation_id
            WHERE generations.semantic_settings_json NOT LIKE
                  '%"pipelineVersion":"progressive-segment-v1"%'
              AND generations.semantic_settings_json NOT LIKE
                  '%"pipelineVersion":"progressive-segment-v2"%'
            """
        )
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
        return repaired + self._recover_invalid_segment_checkpoints(indexed_paths)

    def _recover_invalid_segment_checkpoints(self, indexed_paths: set[str]) -> int:
        repaired = 0
        affected_generations: set[str] = set()
        for row in self.database.fetchall("SELECT * FROM translation_segments"):
            generation_id = str(row["generation_id"])
            page_index = int(row["page_index"])
            segment_index = int(row["segment_index"])
            status = str(row["status"])

            clear_columns: list[str] = []
            rollback_stage: str | None = None
            ocr_ok = bool(row["ocr_path"] and str(row["ocr_path"]) in indexed_paths)
            blocks_ok = bool(row["blocks_path"] and str(row["blocks_path"]) in indexed_paths)
            translations_ok = bool(
                row["translations_path"] and str(row["translations_path"]) in indexed_paths
            )
            translated_ok = bool(
                row["translated_path"] and str(row["translated_path"]) in indexed_paths
            )
            if status == "completed" and translated_ok:
                # Published output is self-contained. Missing intermediate files do
                # not invalidate a completed segment and can wait for normal LRU
                # eviction of the whole chapter bundle.
                continue
            if status == "completed" and not translated_ok:
                clear_columns = ["translated_path"]
                rollback_stage = "rendering"
            elif (row["ocr_path"] or row["blocks_path"]) and not (ocr_ok and blocks_ok):
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
                    "status = 'pending'",
                    "error_stage = NULL",
                    "error_code = NULL",
                    "error_summary = NULL",
                    "updated_at = ?",
                ]
            )
            with self.database.transaction() as connection:
                connection.execute(
                    f"""
                    UPDATE translation_segments SET {", ".join(assignments)}
                    WHERE generation_id = ? AND page_index = ? AND segment_index = ?
                    """,
                    (self._timestamp(), generation_id, page_index, segment_index),
                )
                connection.execute(
                    """
                    DELETE FROM active_translation_segments
                    WHERE generation_id = ? AND page_index = ? AND segment_index = ?
                    """,
                    (generation_id, page_index, segment_index),
                )
            repaired += 1
            affected_generations.add(generation_id)

        invalid_layers = self.database.fetchall(
            """
            SELECT generation_id, page_index, segment_index
            FROM active_translation_segments
            WHERE translated_path NOT IN (SELECT relative_path FROM cache_entries)
            """
        )
        for row in invalid_layers:
            generation_id = str(row["generation_id"])
            page_index = int(row["page_index"])
            segment_index = int(row["segment_index"])
            with self.database.transaction() as connection:
                connection.execute(
                    """
                    DELETE FROM active_translation_segments
                    WHERE generation_id = ? AND page_index = ? AND segment_index = ?
                    """,
                    (generation_id, page_index, segment_index),
                )
                connection.execute(
                    """
                    UPDATE translation_segments SET status = 'pending',
                        translated_path = NULL, translated_version = NULL,
                        updated_at = ?
                    WHERE generation_id = ? AND page_index = ?
                      AND segment_index = ? AND status = 'completed'
                    """,
                    (self._timestamp(), generation_id, page_index, segment_index),
                )
            repaired += 1
            affected_generations.add(generation_id)

        self.database.execute(
            """
            DELETE FROM active_translation_pages
            WHERE generation_id IN (
                SELECT generation_id FROM translation_generations
                WHERE semantic_settings_json LIKE
                      '%"pipelineVersion":"progressive-segment-v1"%'
                   OR semantic_settings_json LIKE
                      '%"pipelineVersion":"progressive-segment-v2"%'
            )
              AND translated_path NOT IN (SELECT relative_path FROM cache_entries)
            """
        )

        for generation_id in affected_generations:
            with self.database.transaction() as connection:
                self._refresh_segment_counts(connection, generation_id, self._timestamp())
                connection.execute(
                    """
                    UPDATE translation_generations SET status = CASE
                            WHEN status IN ('preparing', 'queued', 'running')
                                THEN 'queued'
                            ELSE 'paused'
                        END,
                        stop_requested = 0, current_page_index = NULL,
                        current_segment_index = NULL, updated_at = ?
                    WHERE generation_id = ?
                    """,
                    (self._timestamp(), generation_id),
                )
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
        source_pages: dict[int, str] | None = None,
        progressive: bool = False,
    ) -> str:
        generation_id = uuid.uuid4().hex
        timestamp = self._timestamp()
        initial_status = "preparing" if progressive else "queued"
        with self.database.transaction() as connection:
            connection.execute(
                """
                INSERT INTO translation_generations(
                    generation_id, comic_id, chapter_id,
                    semantic_fingerprint, semantic_settings_json,
                    status, stop_requested, total_pages, completed_pages,
                    failed_pages, created_at, updated_at, kind,
                    planning_complete, total_segments, completed_segments,
                    failed_segments
                ) VALUES (?, ?, ?, ?, ?, ?, 0, ?, 0, 0, ?, ?, ?, 0, 0, 0, 0)
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
                    initial_status,
                    len(page_indexes),
                    timestamp,
                    timestamp,
                    kind,
                ),
            )
            connection.executemany(
                """
                INSERT INTO translation_pages(
                    generation_id, page_index, status, source_url, updated_at
                ) VALUES (?, ?, 'pending', ?, ?)
                """,
                [
                    (
                        generation_id,
                        page_index,
                        (source_pages or {}).get(page_index),
                        timestamp,
                    )
                    for page_index in page_indexes
                ],
            )
        return generation_id

    def latest_generation(self, comic_id: str, chapter_id: str) -> sqlite3.Row | None:
        return self.database.fetchone(
            """
            SELECT * FROM translation_generations
            WHERE comic_id = ? AND chapter_id = ?
            ORDER BY created_at DESC, rowid DESC LIMIT 1
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
            WHERE comic_id = ? AND chapter_id = ?
              AND status IN ('preparing', 'queued')
            ORDER BY created_at ASC, rowid ASC LIMIT 1
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
            ORDER BY created_at DESC, rowid DESC LIMIT 1
            """,
            (comic_id, chapter_id, semantic_fingerprint),
        )

    def active_generation(self, comic_id: str, chapter_id: str) -> sqlite3.Row | None:
        return self.database.fetchone(
            """
            SELECT * FROM translation_generations
            WHERE comic_id = ? AND chapter_id = ?
              AND status IN (
                  'preparing', 'queued', 'running', 'stopping_after_page',
                  'stopping_after_segment'
              )
            ORDER BY created_at ASC, rowid ASC LIMIT 1
            """,
            (comic_id, chapter_id),
        )

    def background_tasks(self) -> list[BackgroundTranslationTask]:
        rows = self.database.fetchall(
            """
            SELECT generations.*,
                COALESCE((
                    SELECT SUM(CASE WHEN prepared = 1 THEN 1 ELSE 0 END)
                    FROM translation_pages prepared_pages
                    WHERE prepared_pages.generation_id = generations.generation_id
                ), 0) prepared_pages,
                current_segments.status current_segment_status,
                current_pages.status current_page_status,
                history.title history_comic_title,
                CASE WHEN history.chapter_id = generations.chapter_id
                    THEN history.chapter_title ELSE NULL END history_chapter_title
            FROM translation_generations generations
            LEFT JOIN translation_segments current_segments
              ON current_segments.generation_id = generations.generation_id
             AND current_segments.page_index = generations.current_page_index
             AND current_segments.segment_index = generations.current_segment_index
            LEFT JOIN translation_pages current_pages
              ON current_pages.generation_id = generations.generation_id
             AND current_pages.page_index = generations.current_page_index
            LEFT JOIN reading_history history
              ON history.comic_id = generations.comic_id
            WHERE generations.status IN (
                'preparing', 'queued', 'running', 'stopping_after_page',
                'stopping_after_segment'
            )
            ORDER BY generations.created_at ASC, generations.rowid ASC
            """
        )
        tasks: list[BackgroundTranslationTask] = []
        seen_chapters: set[tuple[str, str]] = set()
        for row in rows:
            comic_id = str(row["comic_id"])
            chapter_id = str(row["chapter_id"])
            chapter_key = (comic_id, chapter_id)
            if chapter_key in seen_chapters:
                continue
            seen_chapters.add(chapter_key)
            tasks.append(
                BackgroundTranslationTask(
                    comic_id=comic_id,
                    chapter_id=chapter_id,
                    comic_title=str(row["history_comic_title"] or comic_id),
                    chapter_title=str(row["history_chapter_title"] or chapter_id),
                    generation_id=str(row["generation_id"]),
                    kind=str(row["kind"]),
                    status=str(row["status"]),
                    stage=self._background_stage(row),
                    current_page_index=(
                        int(row["current_page_index"])
                        if row["current_page_index"] is not None
                        else None
                    ),
                    current_segment=(
                        CurrentTranslationSegment(
                            page_index=int(row["current_page_index"]),
                            segment_index=int(row["current_segment_index"]),
                        )
                        if row["current_page_index"] is not None
                        and row["current_segment_index"] is not None
                        else None
                    ),
                    planning_complete=bool(row["planning_complete"]),
                    total_pages=int(row["total_pages"]),
                    prepared_pages=int(row["prepared_pages"]),
                    completed_pages=int(row["completed_pages"]),
                    failed_pages=int(row["failed_pages"]),
                    total_segments=int(row["total_segments"]),
                    completed_segments=int(row["completed_segments"]),
                    failed_segments=int(row["failed_segments"]),
                )
            )
        return tasks

    def force_pause_chapter(self, comic_id: str, chapter_id: str) -> int:
        timestamp = self._timestamp()
        with self.database.transaction() as connection:
            rows = connection.execute(
                """
                SELECT generation_id FROM translation_generations
                WHERE comic_id = ? AND chapter_id = ?
                  AND status IN (
                      'preparing', 'queued', 'running', 'stopping_after_page',
                      'stopping_after_segment'
                  )
                ORDER BY created_at ASC, rowid ASC
                """,
                (comic_id, chapter_id),
            ).fetchall()
            generation_ids = [str(row["generation_id"]) for row in rows]
            if not generation_ids:
                return 0
            placeholders = ",".join("?" for _ in generation_ids)
            connection.execute(
                f"""
                UPDATE translation_pages SET status = 'pending', updated_at = ?
                WHERE generation_id IN ({placeholders})
                  AND status IN ('downloading', 'ocr', 'translating', 'rendering')
                """,
                (timestamp, *generation_ids),
            )
            connection.execute(
                f"""
                UPDATE translation_segments SET status = 'pending', updated_at = ?
                WHERE generation_id IN ({placeholders})
                  AND status IN ('ocr', 'translating', 'rendering')
                """,
                (timestamp, *generation_ids),
            )
            connection.execute(
                f"""
                UPDATE translation_generations SET status = 'paused',
                    stop_requested = 0, current_page_index = NULL,
                    current_segment_index = NULL, updated_at = ?
                WHERE generation_id IN ({placeholders})
                """,
                (timestamp, *generation_ids),
            )
            connection.execute(
                """
                UPDATE cache_bundles SET active_task = 0, accessed_at = ?
                WHERE kind = 'chapter' AND comic_id = ? AND chapter_id = ?
                """,
                (timestamp, comic_id, chapter_id),
            )
            return len(generation_ids)

    @staticmethod
    def _background_stage(row: sqlite3.Row) -> str:
        status = str(row["status"])
        if status in {"stopping_after_page", "stopping_after_segment"}:
            return "stopping"
        if status == "preparing":
            return "preparing"
        if status == "queued":
            return "queued"
        current_stage = str(row["current_segment_status"] or row["current_page_status"] or "")
        if current_stage in {"ocr", "translating", "rendering"}:
            return current_stage
        if current_stage == "downloading":
            return "preparing"
        return "processing"

    def set_generation_status(
        self,
        generation_id: str,
        status: str,
        *,
        stop_requested: bool | None = None,
        current_page_index: int | None = None,
        current_segment_index: int | None = None,
    ) -> None:
        fields = [
            "status = ?",
            "current_page_index = ?",
            "current_segment_index = ?",
            "updated_at = ?",
        ]
        parameters: list[object] = [
            status,
            current_page_index,
            current_segment_index,
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

    def begin_preparing(self, generation_id: str) -> bool:
        return bool(
            self.database.execute(
                """
                UPDATE translation_generations SET status = 'preparing',
                    current_page_index = NULL, current_segment_index = NULL,
                    updated_at = ?
                WHERE generation_id = ? AND status IN ('preparing', 'queued')
                  AND stop_requested = 0
                """,
                (self._timestamp(), generation_id),
            )
        )

    def begin_running(self, generation_id: str) -> bool:
        return bool(
            self.database.execute(
                """
                UPDATE translation_generations SET status = 'running',
                    current_page_index = NULL, current_segment_index = NULL,
                    updated_at = ?
                WHERE generation_id = ? AND status IN ('preparing', 'queued')
                  AND stop_requested = 0
                """,
                (self._timestamp(), generation_id),
            )
        )

    def request_stop(self, generation_id: str) -> str | None:
        row = self.generation(generation_id)
        if row is None:
            return None
        status = str(row["status"])
        if status == "queued":
            self.set_generation_status(generation_id, "paused", stop_requested=False)
            return "paused"
        if status == "preparing":
            self.set_generation_status(
                generation_id,
                "stopping_after_segment",
                stop_requested=True,
            )
            return "stopping_after_segment"
        if status == "running":
            progressive = bool(row["planning_complete"]) or int(row["total_segments"]) > 0
            stopping_status = "stopping_after_segment" if progressive else "stopping_after_page"
            self.set_generation_status(
                generation_id,
                stopping_status,
                stop_requested=True,
                current_page_index=(
                    int(row["current_page_index"])
                    if row["current_page_index"] is not None
                    else None
                ),
                current_segment_index=(
                    int(row["current_segment_index"])
                    if row["current_segment_index"] is not None
                    else None
                ),
            )
            return stopping_status
        return status

    def resume(self, generation_id: str) -> None:
        row = self.generation(generation_id)
        status = "queued" if row is not None and bool(row["planning_complete"]) else "preparing"
        self.set_generation_status(generation_id, status, stop_requested=False)

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

    def save_prepared_page(
        self,
        generation_id: str,
        page_index: int,
        *,
        source_url: str,
        original_path: str,
        original_checksum: str,
        width: int,
        height: int,
    ) -> None:
        self.database.execute(
            """
            UPDATE translation_pages SET source_url = ?, original_path = ?,
                original_checksum = ?, width = ?, height = ?, prepared = 1,
                status = 'pending', updated_at = ?
            WHERE generation_id = ? AND page_index = ?
            """,
            (
                source_url,
                original_path,
                original_checksum,
                width,
                height,
                self._timestamp(),
                generation_id,
                page_index,
            ),
        )

    def append_prepared_page_segments(
        self,
        generation_id: str,
        page_index: int,
        *,
        source_url: str,
        original_path: str,
        original_checksum: str,
        width: int,
        height: int,
        segments: list[dict[str, object]],
    ) -> int:
        """Atomically publish one prepared page and its newly discovered segments."""
        if not segments:
            raise ValueError("prepared page must contain at least one segment")
        if any(int(segment["page_index"]) != page_index for segment in segments):
            raise ValueError("prepared page segments do not belong to the page")

        timestamp = self._timestamp()
        with self.database.transaction() as connection:
            page = connection.execute(
                """
                SELECT prepared FROM translation_pages
                WHERE generation_id = ? AND page_index = ?
                """,
                (generation_id, page_index),
            ).fetchone()
            if page is None:
                raise ValueError("translation page does not exist")

            existing_count = int(
                connection.execute(
                    """
                    SELECT COUNT(*) FROM translation_segments
                    WHERE generation_id = ? AND page_index = ?
                    """,
                    (generation_id, page_index),
                ).fetchone()[0]
            )
            if bool(page["prepared"]):
                if existing_count != len(segments):
                    raise ValueError("prepared page segment plan is inconsistent")
                return 0
            if existing_count:
                raise ValueError("unprepared page already contains segments")

            next_global_index = int(
                connection.execute(
                    """
                    SELECT COALESCE(MAX(global_index) + 1, 0)
                    FROM translation_segments WHERE generation_id = ?
                    """,
                    (generation_id,),
                ).fetchone()[0]
            )
            connection.executemany(
                """
                INSERT INTO translation_segments(
                    generation_id, page_index, segment_index, global_index,
                    status, source_width, source_height, display_top,
                    display_bottom, ocr_top, ocr_bottom, ocr_input_path,
                    created_at, updated_at
                ) VALUES (?, ?, ?, ?, 'pending', ?, ?, ?, ?, ?, ?, ?, ?, ?)
                """,
                [
                    (
                        generation_id,
                        page_index,
                        int(segment["segment_index"]),
                        next_global_index + offset,
                        int(segment["source_width"]),
                        int(segment["source_height"]),
                        int(segment["display_top"]),
                        int(segment["display_bottom"]),
                        int(segment["ocr_top"]),
                        int(segment["ocr_bottom"]),
                        str(segment["ocr_input_path"]),
                        timestamp,
                        timestamp,
                    )
                    for offset, segment in enumerate(segments)
                ],
            )
            connection.execute(
                """
                UPDATE translation_pages SET source_url = ?, original_path = ?,
                    original_checksum = ?, width = ?, height = ?, prepared = 1,
                    status = 'pending', updated_at = ?
                WHERE generation_id = ? AND page_index = ?
                """,
                (
                    source_url,
                    original_path,
                    original_checksum,
                    width,
                    height,
                    timestamp,
                    generation_id,
                    page_index,
                ),
            )
            connection.execute(
                """
                UPDATE translation_generations SET total_segments = (
                    SELECT COUNT(*) FROM translation_segments
                    WHERE generation_id = ?
                ), updated_at = ? WHERE generation_id = ?
                """,
                (generation_id, timestamp, generation_id),
            )
        return len(segments)

    def complete_segment_plan(self, generation_id: str) -> None:
        self.database.execute(
            """
            UPDATE translation_generations SET planning_complete = 1,
                total_segments = (
                    SELECT COUNT(*) FROM translation_segments
                    WHERE generation_id = ?
                ), updated_at = ? WHERE generation_id = ?
            """,
            (generation_id, self._timestamp(), generation_id),
        )

    def commit_segment_plan(
        self,
        generation_id: str,
        segments: list[dict[str, object]],
    ) -> None:
        timestamp = self._timestamp()
        with self.database.transaction() as connection:
            connection.execute(
                "DELETE FROM translation_segments WHERE generation_id = ?",
                (generation_id,),
            )
            connection.executemany(
                """
                INSERT INTO translation_segments(
                    generation_id, page_index, segment_index, global_index,
                    status, source_width, source_height, display_top,
                    display_bottom, ocr_top, ocr_bottom, ocr_input_path,
                    created_at, updated_at
                ) VALUES (?, ?, ?, ?, 'pending', ?, ?, ?, ?, ?, ?, ?, ?, ?)
                """,
                [
                    (
                        generation_id,
                        int(segment["page_index"]),
                        int(segment["segment_index"]),
                        int(segment["global_index"]),
                        int(segment["source_width"]),
                        int(segment["source_height"]),
                        int(segment["display_top"]),
                        int(segment["display_bottom"]),
                        int(segment["ocr_top"]),
                        int(segment["ocr_bottom"]),
                        str(segment["ocr_input_path"]),
                        timestamp,
                        timestamp,
                    )
                    for segment in segments
                ],
            )
            connection.execute(
                """
                UPDATE translation_generations SET planning_complete = 1,
                    total_segments = ?, completed_segments = 0,
                    failed_segments = 0, status = 'queued',
                    current_page_index = NULL, current_segment_index = NULL,
                    updated_at = ? WHERE generation_id = ?
                """,
                (len(segments), timestamp, generation_id),
            )

    def segment(
        self,
        generation_id: str,
        page_index: int,
        segment_index: int,
    ) -> sqlite3.Row | None:
        return self.database.fetchone(
            """
            SELECT * FROM translation_segments
            WHERE generation_id = ? AND page_index = ? AND segment_index = ?
            """,
            (generation_id, page_index, segment_index),
        )

    def segments(self, generation_id: str) -> list[sqlite3.Row]:
        return self.database.fetchall(
            """
            SELECT * FROM translation_segments WHERE generation_id = ?
            ORDER BY global_index
            """,
            (generation_id,),
        )

    def pending_segments(self, generation_id: str) -> list[sqlite3.Row]:
        return self.database.fetchall(
            """
            SELECT * FROM translation_segments
            WHERE generation_id = ? AND status = 'pending'
            ORDER BY global_index
            """,
            (generation_id,),
        )

    def set_segment_stage(
        self,
        generation_id: str,
        page_index: int,
        segment_index: int,
        status: str,
        *,
        increment_attempts: bool = False,
        paths: dict[str, str | None] | None = None,
        job_id: str | None = None,
    ) -> None:
        fields = ["status = ?", "updated_at = ?"]
        parameters: list[object] = [status, self._timestamp()]
        if increment_attempts:
            fields.append("attempts = attempts + 1")
        allowed = {
            "ocr_input_path",
            "ocr_path",
            "blocks_path",
            "translations_path",
            "translated_path",
        }
        for key, value in (paths or {}).items():
            if key not in allowed:
                raise ValueError("unsupported segment checkpoint column")
            fields.append(f"{key} = ?")
            parameters.append(value)
        if job_id is not None:
            fields.append("ocr_job_id = ?")
            parameters.append(job_id)
        fields.extend(["error_stage = NULL", "error_code = NULL", "error_summary = NULL"])
        parameters.extend([generation_id, page_index, segment_index])
        self.database.execute(
            f"""
            UPDATE translation_segments SET {", ".join(fields)}
            WHERE generation_id = ? AND page_index = ? AND segment_index = ?
            """,
            tuple(parameters),
        )

    def set_segment_job_id(
        self,
        generation_id: str,
        page_index: int,
        segment_index: int,
        job_id: str | None,
    ) -> None:
        self.database.execute(
            """
            UPDATE translation_segments SET ocr_job_id = ?, updated_at = ?
            WHERE generation_id = ? AND page_index = ? AND segment_index = ?
            """,
            (job_id, self._timestamp(), generation_id, page_index, segment_index),
        )

    def complete_segment(
        self,
        generation_id: str,
        comic_id: str,
        chapter_id: str,
        page_index: int,
        segment_index: int,
        *,
        translated_path: str,
        translated_version: str,
    ) -> None:
        timestamp = self._timestamp()
        with self.database.transaction() as connection:
            row = connection.execute(
                """
                SELECT * FROM translation_segments
                WHERE generation_id = ? AND page_index = ? AND segment_index = ?
                """,
                (generation_id, page_index, segment_index),
            ).fetchone()
            if row is None:
                raise ValueError("translation segment does not exist")
            connection.execute(
                """
                UPDATE translation_segments SET status = 'completed',
                    translated_path = ?, translated_version = ?,
                    error_stage = NULL, error_code = NULL,
                    error_summary = NULL, updated_at = ?
                WHERE generation_id = ? AND page_index = ? AND segment_index = ?
                """,
                (
                    translated_path,
                    translated_version,
                    timestamp,
                    generation_id,
                    page_index,
                    segment_index,
                ),
            )
            connection.execute(
                """
                INSERT INTO active_translation_segments(
                    comic_id, chapter_id, page_index, generation_id,
                    segment_index, display_top, display_bottom, source_width,
                    source_height, translated_path, translated_version, published_at
                ) VALUES (?, ?, ?, ?, ?, ?, ?, ?, ?, ?, ?, ?)
                ON CONFLICT(
                    comic_id, chapter_id, page_index, generation_id, segment_index
                ) DO UPDATE SET
                    display_top = excluded.display_top,
                    display_bottom = excluded.display_bottom,
                    source_width = excluded.source_width,
                    source_height = excluded.source_height,
                    translated_path = excluded.translated_path,
                    translated_version = excluded.translated_version,
                    published_at = excluded.published_at
                """,
                (
                    comic_id,
                    chapter_id,
                    page_index,
                    generation_id,
                    segment_index,
                    int(row["display_top"]),
                    int(row["display_bottom"]),
                    int(row["source_width"]),
                    int(row["source_height"]),
                    translated_path,
                    translated_version,
                    timestamp,
                ),
            )
            self._refresh_segment_counts(connection, generation_id, timestamp)

    def fail_segment(
        self,
        generation_id: str,
        page_index: int,
        segment_index: int,
        *,
        stage: str,
        code: str,
        summary: str,
    ) -> None:
        timestamp = self._timestamp()
        with self.database.transaction() as connection:
            connection.execute(
                """
                UPDATE translation_segments SET status = 'failed',
                    error_stage = ?, error_code = ?, error_summary = ?, updated_at = ?
                WHERE generation_id = ? AND page_index = ? AND segment_index = ?
                """,
                (stage, code, summary[:500], timestamp, generation_id, page_index, segment_index),
            )
            self._refresh_segment_counts(connection, generation_id, timestamp)

    def prepare_segment_retry(
        self,
        generation_id: str,
        page_index: int,
        segment_index: int,
        *,
        clear_columns: list[str],
        clear_job_id: bool = False,
    ) -> list[str]:
        row = self.segment(generation_id, page_index, segment_index)
        if row is None:
            return []
        allowed = {"ocr_path", "blocks_path", "translations_path", "translated_path"}
        columns = [column for column in clear_columns if column in allowed]
        paths = [str(row[column]) for column in columns if row[column]]
        assignments = [f"{column} = NULL" for column in columns]
        assignments.extend(
            [
                "status = 'pending'",
                "translated_version = NULL",
                "error_stage = NULL",
                "error_code = NULL",
                "error_summary = NULL",
                "updated_at = ?",
            ]
        )
        if clear_job_id:
            assignments.append("ocr_job_id = NULL")
        with self.database.transaction() as connection:
            connection.execute(
                f"""
                UPDATE translation_segments SET {", ".join(assignments)}
                WHERE generation_id = ? AND page_index = ? AND segment_index = ?
                """,
                (self._timestamp(), generation_id, page_index, segment_index),
            )
            connection.execute(
                """
                UPDATE translation_pages SET status = 'pending',
                    error_stage = NULL, error_code = NULL,
                    error_summary = NULL, updated_at = ?
                WHERE generation_id = ? AND page_index = ?
                """,
                (self._timestamp(), generation_id, page_index),
            )
            self._refresh_segment_counts(connection, generation_id, self._timestamp())
        return paths

    @staticmethod
    def _refresh_segment_counts(
        connection: sqlite3.Connection,
        generation_id: str,
        timestamp: int,
    ) -> None:
        counts = connection.execute(
            """
            SELECT
                SUM(CASE WHEN status = 'completed' THEN 1 ELSE 0 END) completed,
                SUM(CASE WHEN status = 'failed' THEN 1 ELSE 0 END) failed
            FROM translation_segments WHERE generation_id = ?
            """,
            (generation_id,),
        ).fetchone()
        connection.execute(
            """
            UPDATE translation_generations SET completed_segments = ?,
                failed_segments = ?, updated_at = ? WHERE generation_id = ?
            """,
            (
                int(counts["completed"] or 0) if counts else 0,
                int(counts["failed"] or 0) if counts else 0,
                timestamp,
                generation_id,
            ),
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

    def finalize_page_from_segments(
        self,
        generation_id: str,
        page_index: int,
        *,
        failed_stage: str = "segment",
    ) -> str:
        counts = self.database.fetchone(
            """
            SELECT COUNT(*) total,
                SUM(CASE WHEN status = 'completed' THEN 1 ELSE 0 END) completed,
                SUM(CASE WHEN status = 'failed' THEN 1 ELSE 0 END) failed
            FROM translation_segments
            WHERE generation_id = ? AND page_index = ?
            """,
            (generation_id, page_index),
        )
        total = int(counts["total"] or 0) if counts else 0
        completed = int(counts["completed"] or 0) if counts else 0
        failed = int(counts["failed"] or 0) if counts else 0
        if total == 0 or completed + failed < total:
            return "pending"
        if failed:
            self.database.execute(
                """
                UPDATE translation_pages SET status = 'failed',
                    error_stage = ?, error_code = 'SEGMENTS_FAILED',
                    error_summary = ?, updated_at = ?
                WHERE generation_id = ? AND page_index = ?
                """,
                (
                    failed_stage,
                    f"{failed} 个翻译分片失败",
                    self._timestamp(),
                    generation_id,
                    page_index,
                ),
            )
            self.refresh_counts(generation_id)
            return "failed"
        return "ready"

    def publish_completed_segment_page(
        self,
        generation_id: str,
        comic_id: str,
        chapter_id: str,
        page_index: int,
    ) -> bool:
        timestamp = self._timestamp()
        with self.database.transaction() as connection:
            counts = connection.execute(
                """
                SELECT COUNT(*) total,
                    SUM(CASE WHEN status = 'completed' THEN 1 ELSE 0 END) completed
                FROM translation_segments
                WHERE generation_id = ? AND page_index = ?
                """,
                (generation_id, page_index),
            ).fetchone()
            total = int(counts["total"] or 0) if counts else 0
            completed = int(counts["completed"] or 0) if counts else 0
            if total == 0 or completed != total:
                return False
            connection.execute(
                """
                UPDATE translation_pages SET status = 'completed',
                    translated_path = NULL, translated_version = NULL,
                    display_parts_json = '[]', error_stage = NULL,
                    error_code = NULL, error_summary = NULL, updated_at = ?
                WHERE generation_id = ? AND page_index = ?
                """,
                (timestamp, generation_id, page_index),
            )
            connection.execute(
                """
                DELETE FROM active_translation_segments
                WHERE comic_id = ? AND chapter_id = ? AND page_index = ?
                  AND generation_id != ?
                """,
                (comic_id, chapter_id, page_index, generation_id),
            )
            connection.execute(
                """
                DELETE FROM active_translation_pages
                WHERE comic_id = ? AND chapter_id = ? AND page_index = ?
                """,
                (comic_id, chapter_id, page_index),
            )
        self.refresh_counts(generation_id)
        return True

    def discard_older_active_segments(
        self,
        comic_id: str,
        chapter_id: str,
        page_index: int,
        generation_id: str,
    ) -> None:
        self.database.execute(
            """
            DELETE FROM active_translation_segments
            WHERE comic_id = ? AND chapter_id = ? AND page_index = ?
              AND generation_id != ?
            """,
            (comic_id, chapter_id, page_index, generation_id),
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
            current_segment=(
                CurrentTranslationSegment(
                    page_index=int(generation["current_page_index"]),
                    segment_index=int(generation["current_segment_index"]),
                )
                if generation["current_page_index"] is not None
                and generation["current_segment_index"] is not None
                else None
            ),
            total_pages=int(generation["total_pages"]),
            completed_pages=int(generation["completed_pages"]),
            failed_pages=int(generation["failed_pages"]),
            planning_complete=bool(generation["planning_complete"]),
            total_segments=int(generation["total_segments"]),
            completed_segments=int(generation["completed_segments"]),
            failed_segments=int(generation["failed_segments"]),
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

    def active_segment(
        self,
        comic_id: str,
        chapter_id: str,
        page_index: int,
        segment_index: int,
        version: str,
    ) -> sqlite3.Row | None:
        return self.database.fetchone(
            """
            SELECT * FROM active_translation_segments
            WHERE comic_id = ? AND chapter_id = ? AND page_index = ?
              AND segment_index = ? AND translated_version = ?
            ORDER BY published_at DESC LIMIT 1
            """,
            (comic_id, chapter_id, page_index, segment_index, version),
        )

    def translation_layers(
        self,
        comic_id: str,
        chapter_id: str,
        page_index: int,
    ) -> list[TranslationLayer]:
        segment_rows = self.database.fetchall(
            """
            SELECT active.*, generations.created_at generation_created_at,
                generations.rowid generation_order
            FROM active_translation_segments active
            JOIN translation_generations generations
              ON generations.generation_id = active.generation_id
            WHERE active.comic_id = ? AND active.chapter_id = ?
              AND active.page_index = ?
            ORDER BY generations.created_at, generations.rowid, active.published_at,
                active.generation_id, active.segment_index
            """,
            (comic_id, chapter_id, page_index),
        )
        segment_generation_ids = {str(row["generation_id"]) for row in segment_rows}
        layers: list[TranslationLayer] = []
        page = self.active_page(comic_id, chapter_id, page_index)
        if (
            page is not None
            and str(page["generation_id"]) not in segment_generation_ids
            and page["width"] is not None
            and page["height"] is not None
        ):
            width = int(page["width"])
            height = int(page["height"])
            version = str(page["translated_version"])
            layers.append(
                TranslationLayer(
                    kind="page",
                    generation_id=str(page["generation_id"]),
                    top=0,
                    bottom=height,
                    source_width=width,
                    source_height=height,
                    url=self.translated_url(comic_id, chapter_id, page_index, version),
                    version=version,
                )
            )
        layers.extend(
            TranslationLayer(
                kind="segment",
                generation_id=str(row["generation_id"]),
                segment_index=int(row["segment_index"]),
                top=int(row["display_top"]),
                bottom=int(row["display_bottom"]),
                source_width=int(row["source_width"]),
                source_height=int(row["source_height"]),
                url=self.translated_segment_url(
                    comic_id,
                    chapter_id,
                    page_index,
                    int(row["segment_index"]),
                    str(row["translated_version"]),
                ),
                version=str(row["translated_version"]),
            )
            for row in segment_rows
        )
        return layers

    def _page_states(
        self, comic_id: str, chapter_id: str, generation_id: str
    ) -> list[TranslationPageState]:
        rows = self.database.fetchall(
            """
            SELECT pages.*, active.translated_path active_path,
                active.translated_version active_version,
                active_pages.width active_width,
                active_pages.height active_height,
                active_pages.display_parts_json active_display_parts_json
            FROM translation_pages pages
            LEFT JOIN active_translation_pages active
              ON active.comic_id = ? AND active.chapter_id = ?
             AND active.page_index = pages.page_index
            LEFT JOIN translation_pages active_pages
              ON active_pages.generation_id = active.generation_id
             AND active_pages.page_index = active.page_index
            WHERE pages.generation_id = ? ORDER BY pages.page_index
            """,
            (comic_id, chapter_id, generation_id),
        )
        segment_rows = self.segments(generation_id)
        segments_by_page: dict[int, list[TranslationSegmentState]] = {}
        for row in segment_rows:
            page_index = int(row["page_index"])
            version = str(row["translated_version"]) if row["translated_version"] else None
            segments_by_page.setdefault(page_index, []).append(
                TranslationSegmentState(
                    page_index=page_index,
                    segment_index=int(row["segment_index"]),
                    global_index=int(row["global_index"]),
                    status=str(row["status"]),
                    display_top=int(row["display_top"]),
                    display_bottom=int(row["display_bottom"]),
                    source_width=int(row["source_width"]),
                    source_height=int(row["source_height"]),
                    translated_url=(
                        self.translated_segment_url(
                            comic_id,
                            chapter_id,
                            page_index,
                            int(row["segment_index"]),
                            version,
                        )
                        if version
                        else None
                    ),
                    translated_version=version,
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
                translated_part_urls=(
                    self.translated_part_urls(
                        comic_id,
                        chapter_id,
                        int(row["page_index"]),
                        str(row["active_version"]),
                        len(self.decode_display_parts(row["active_display_parts_json"])),
                    )
                    if row["active_path"] and row["active_version"]
                    else []
                ),
                translated_version=(str(row["active_version"]) if row["active_version"] else None),
                width=(
                    int(row["active_width"])
                    if row["active_width"] is not None
                    else (int(row["width"]) if row["width"] is not None else None)
                ),
                height=(
                    int(row["active_height"])
                    if row["active_height"] is not None
                    else (int(row["height"]) if row["height"] is not None else None)
                ),
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
                segments=segments_by_page.get(int(row["page_index"]), []),
                translation_layers=self.translation_layers(
                    comic_id,
                    chapter_id,
                    int(row["page_index"]),
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
    def translated_part_urls(
        comic_id: str,
        chapter_id: str,
        page_index: int,
        version: str,
        part_count: int,
    ) -> list[str]:
        base = (
            f"/api/media/comics/{comic_id}/chapters/{chapter_id}/pages/"
            f"{page_index}/translated/parts"
        )
        return [f"{base}/{part_index}?v={version}" for part_index in range(part_count)]

    @staticmethod
    def translated_segment_url(
        comic_id: str,
        chapter_id: str,
        page_index: int,
        segment_index: int,
        version: str,
    ) -> str:
        return (
            f"/api/media/comics/{comic_id}/chapters/{chapter_id}/pages/"
            f"{page_index}/segments/{segment_index}/translated?v={version}"
        )

    @staticmethod
    def decode_display_parts(value: object) -> list[str]:
        if value is None:
            return []
        try:
            decoded = json.loads(str(value))
        except (TypeError, ValueError, json.JSONDecodeError):
            return []
        if not isinstance(decoded, list):
            return []
        return [str(path) for path in decoded if isinstance(path, str)]

    @staticmethod
    def decode_semantic_settings(row: sqlite3.Row) -> dict[str, Any]:
        value = json.loads(str(row["semantic_settings_json"]))
        return value if isinstance(value, dict) else {}

    @staticmethod
    def _timestamp() -> int:
        return time.time_ns() // 1_000_000
