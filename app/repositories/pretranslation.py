from __future__ import annotations

import sqlite3
import time
import uuid
from collections.abc import Sequence

from app.domain.pretranslation import (
    TranslationBatchItemStatus,
    TranslationBatchItemSummary,
    TranslationBatchSummary,
)
from app.domain.translation import TranslationTaskState
from app.repositories.database import Database

OPEN_BATCH_STATUSES = {
    "queued",
    "running",
    "pausing",
    "paused",
    "cancelling",
    "completed_with_errors",
    "failed",
}
ACTIVE_BATCH_STATUSES = {"queued", "running", "pausing", "cancelling"}
TERMINAL_ITEM_STATUSES = {"completed", "skipped", "failed", "cancelled"}


class PretranslationRepository:
    def __init__(self, database: Database) -> None:
        self.database = database

    def create_batch(
        self,
        comic_id: str,
        comic_title: str,
        chapters: Sequence[tuple[str, str]],
    ) -> str:
        if not chapters:
            raise ValueError("A translation batch requires at least one chapter")
        batch_id = uuid.uuid4().hex
        timestamp = self._timestamp()
        with self.database.transaction() as connection:
            connection.execute(
                """
                INSERT INTO translation_batches(
                    batch_id, comic_id, comic_title, status,
                    interactive_yielded, created_at, updated_at
                ) VALUES (?, ?, ?, 'queued', 0, ?, ?)
                """,
                (batch_id, comic_id, comic_title, timestamp, timestamp),
            )
            connection.executemany(
                """
                INSERT INTO translation_batch_items(
                    batch_item_id, batch_id, chapter_id, chapter_title,
                    position, status, attempts, created_at, updated_at
                ) VALUES (?, ?, ?, ?, ?, 'pending', 0, ?, ?)
                """,
                [
                    (
                        uuid.uuid4().hex,
                        batch_id,
                        chapter_id,
                        chapter_title,
                        position,
                        timestamp,
                        timestamp,
                    )
                    for position, (chapter_id, chapter_title) in enumerate(chapters)
                ],
            )
        return batch_id

    def batch(self, batch_id: str) -> sqlite3.Row | None:
        return self.database.fetchone(
            "SELECT * FROM translation_batches WHERE batch_id = ?",
            (batch_id,),
        )

    def open_batch_for_comic(self, comic_id: str) -> sqlite3.Row | None:
        return self.database.fetchone(
            """
            SELECT * FROM translation_batches
            WHERE comic_id = ? AND status NOT IN ('completed', 'cancelled')
            ORDER BY created_at ASC, rowid ASC LIMIT 1
            """,
            (comic_id,),
        )

    def batch_item(self, batch_item_id: str) -> sqlite3.Row | None:
        return self.database.fetchone(
            "SELECT * FROM translation_batch_items WHERE batch_item_id = ?",
            (batch_item_id,),
        )

    def batch_items(self, batch_id: str) -> list[sqlite3.Row]:
        return self.database.fetchall(
            """
            SELECT * FROM translation_batch_items
            WHERE batch_id = ? ORDER BY position ASC
            """,
            (batch_id,),
        )

    def batch_item_summaries(self, batch_id: str) -> list[TranslationBatchItemSummary]:
        return [self._item_summary(row) for row in self.batch_items(batch_id)]

    def current_item(self, batch_id: str) -> sqlite3.Row | None:
        return self.database.fetchone(
            """
            SELECT * FROM translation_batch_items
            WHERE batch_id = ? AND status = 'running'
            ORDER BY position ASC LIMIT 1
            """,
            (batch_id,),
        )

    def next_pending_item(self, batch_id: str) -> sqlite3.Row | None:
        return self.database.fetchone(
            """
            SELECT * FROM translation_batch_items
            WHERE batch_id = ? AND status = 'pending'
            ORDER BY position ASC LIMIT 1
            """,
            (batch_id,),
        )

    def owned_generation(self, batch_item_id: str) -> sqlite3.Row | None:
        return self.database.fetchone(
            """
            SELECT * FROM translation_generations
            WHERE batch_item_id = ? LIMIT 1
            """,
            (batch_item_id,),
        )

    def owned_unfinished_item(self, batch_id: str) -> sqlite3.Row | None:
        return self.database.fetchone(
            """
            SELECT items.* FROM translation_batch_items items
            JOIN translation_generations generations
              ON generations.batch_item_id = items.batch_item_id
            WHERE items.batch_id = ?
              AND items.status IN ('pending', 'running')
              AND generations.status IN (
                  'preparing', 'queued', 'running',
                  'stopping_after_page', 'stopping_after_segment', 'paused'
              )
            ORDER BY items.position ASC LIMIT 1
            """,
            (batch_id,),
        )

    def scheduler_batch(self) -> sqlite3.Row | None:
        return self.database.fetchone(
            """
            SELECT * FROM translation_batches
            WHERE status IN ('queued', 'running', 'pausing', 'cancelling')
            ORDER BY CASE
                    WHEN status IN ('running', 'pausing', 'cancelling') THEN 0
                    ELSE 1
                END,
                created_at ASC, rowid ASC
            LIMIT 1
            """
        )

    def interactive_yielded_batch(self) -> sqlite3.Row | None:
        return self.database.fetchone(
            """
            SELECT * FROM translation_batches
            WHERE interactive_yielded = 1
              AND status NOT IN ('completed', 'cancelled')
            ORDER BY created_at ASC, rowid ASC LIMIT 1
            """
        )

    def claim_batch(self, batch_id: str) -> bool:
        timestamp = self._timestamp()
        changed = self.database.execute(
            """
            UPDATE translation_batches
            SET status = 'running', pause_reason = NULL,
                error_code = NULL, error_summary = NULL, updated_at = ?
            WHERE batch_id = ? AND status = 'queued'
            """,
            (timestamp, batch_id),
        )
        if changed:
            return True
        row = self.batch(batch_id)
        return row is not None and str(row["status"]) == "running"

    def claim_item(self, batch_item_id: str) -> bool:
        return bool(
            self.database.execute(
                """
                UPDATE translation_batch_items
                SET status = 'running', attempts = attempts + 1,
                    error_code = NULL, error_summary = NULL, updated_at = ?
                WHERE batch_item_id = ? AND status = 'pending'
                  AND NOT EXISTS (
                      SELECT 1 FROM translation_batch_items current
                      WHERE current.batch_id = translation_batch_items.batch_id
                        AND current.status = 'running'
                  )
                """,
                (self._timestamp(), batch_item_id),
            )
        )

    def finish_item(
        self,
        batch_item_id: str,
        status: TranslationBatchItemStatus,
        *,
        error_code: str | None = None,
        error_summary: str | None = None,
    ) -> bool:
        if status not in TERMINAL_ITEM_STATUSES:
            raise ValueError(f"Invalid terminal batch item status: {status}")
        return bool(
            self.database.execute(
                """
                UPDATE translation_batch_items
                SET status = ?, error_code = ?, error_summary = ?, updated_at = ?
                WHERE batch_item_id = ? AND status = 'running'
                """,
                (
                    status,
                    error_code,
                    error_summary,
                    self._timestamp(),
                    batch_item_id,
                ),
            )
        )

    def set_item_pending(self, batch_item_id: str) -> bool:
        return bool(
            self.database.execute(
                """
                UPDATE translation_batch_items
                SET status = 'pending', error_code = NULL,
                    error_summary = NULL, updated_at = ?
                WHERE batch_item_id = ? AND status = 'running'
                """,
                (self._timestamp(), batch_item_id),
            )
        )

    def request_pause(self, batch_id: str, *, reason: str = "user") -> sqlite3.Row | None:
        if reason not in {"user", "config"}:
            raise ValueError(f"Invalid pause reason: {reason}")
        timestamp = self._timestamp()
        with self.database.transaction() as connection:
            row = connection.execute(
                "SELECT * FROM translation_batches WHERE batch_id = ?",
                (batch_id,),
            ).fetchone()
            if row is None:
                return None
            status = str(row["status"])
            if status in {"completed", "completed_with_errors", "cancelled", "failed"}:
                return row
            has_running = bool(
                connection.execute(
                    """
                    SELECT 1 FROM translation_batch_items
                    WHERE batch_id = ? AND status = 'running' LIMIT 1
                    """,
                    (batch_id,),
                ).fetchone()
            )
            next_status = (
                "pausing"
                if has_running and reason == "user" and not bool(row["interactive_yielded"])
                else "paused"
            )
            if (
                next_status == "paused"
                and has_running
                and reason == "user"
                and bool(row["interactive_yielded"])
            ):
                connection.execute(
                    """
                    UPDATE translation_batch_items
                    SET status = 'pending', updated_at = ?
                    WHERE batch_id = ? AND status = 'running'
                    """,
                    (timestamp, batch_id),
                )
            connection.execute(
                """
                UPDATE translation_batches
                SET status = ?, pause_reason = ?, updated_at = ?
                WHERE batch_id = ?
                """,
                (next_status, reason, timestamp, batch_id),
            )
        return self.batch(batch_id)

    def pause_for_config(
        self,
        batch_id: str,
        batch_item_id: str,
        *,
        error_code: str,
        error_summary: str,
    ) -> sqlite3.Row | None:
        timestamp = self._timestamp()
        with self.database.transaction() as connection:
            row = connection.execute(
                "SELECT * FROM translation_batches WHERE batch_id = ?",
                (batch_id,),
            ).fetchone()
            if row is None:
                return None
            connection.execute(
                """
                UPDATE translation_batch_items
                SET status = 'pending', error_code = NULL,
                    error_summary = NULL, updated_at = ?
                WHERE batch_item_id = ? AND batch_id = ? AND status = 'running'
                """,
                (timestamp, batch_item_id, batch_id),
            )
            connection.execute(
                """
                UPDATE translation_batches
                SET status = 'paused', pause_reason = 'config',
                    error_code = ?, error_summary = ?, updated_at = ?
                WHERE batch_id = ? AND status NOT IN ('completed', 'cancelled')
                """,
                (error_code, error_summary, timestamp, batch_id),
            )
        return self.batch(batch_id)

    def resume_batch(self, batch_id: str) -> sqlite3.Row | None:
        timestamp = self._timestamp()
        self.database.execute(
            """
            UPDATE translation_batches
            SET status = 'queued', pause_reason = NULL,
                error_code = NULL, error_summary = NULL, updated_at = ?
            WHERE batch_id = ? AND status IN ('paused', 'failed')
            """,
            (timestamp, batch_id),
        )
        return self.batch(batch_id)

    def cancel_pending(self, batch_id: str) -> sqlite3.Row | None:
        timestamp = self._timestamp()
        with self.database.transaction() as connection:
            row = connection.execute(
                "SELECT * FROM translation_batches WHERE batch_id = ?",
                (batch_id,),
            ).fetchone()
            if row is None:
                return None
            status = str(row["status"])
            if status in {"completed", "cancelled"}:
                return row
            if bool(row["interactive_yielded"]):
                connection.execute(
                    """
                    UPDATE translation_batch_items
                    SET status = 'running', updated_at = ?
                    WHERE batch_id = ? AND status = 'pending'
                      AND EXISTS (
                          SELECT 1 FROM translation_generations generations
                          WHERE generations.batch_item_id =
                                translation_batch_items.batch_item_id
                            AND generations.status IN (
                                'preparing', 'queued', 'running',
                                'stopping_after_page', 'stopping_after_segment', 'paused'
                            )
                      )
                    """,
                    (timestamp, batch_id),
                )
            connection.execute(
                """
                UPDATE translation_batch_items
                SET status = 'cancelled', updated_at = ?
                WHERE batch_id = ? AND status = 'pending'
                """,
                (timestamp, batch_id),
            )
            has_running = bool(
                connection.execute(
                    """
                    SELECT 1 FROM translation_batch_items
                    WHERE batch_id = ? AND status = 'running' LIMIT 1
                    """,
                    (batch_id,),
                ).fetchone()
            )
            connection.execute(
                """
                UPDATE translation_batches
                SET status = ?, pause_reason = NULL,
                    interactive_yielded = CASE WHEN ? THEN interactive_yielded ELSE 0 END,
                    updated_at = ?
                WHERE batch_id = ?
                """,
                (
                    "cancelling" if has_running else "cancelled",
                    int(has_running),
                    timestamp,
                    batch_id,
                ),
            )
        return self.batch(batch_id)

    def retry_failed(self, batch_id: str) -> tuple[sqlite3.Row | None, int]:
        timestamp = self._timestamp()
        with self.database.transaction() as connection:
            row = connection.execute(
                "SELECT * FROM translation_batches WHERE batch_id = ?",
                (batch_id,),
            ).fetchone()
            if row is None:
                return None, 0
            if str(row["status"]) != "completed_with_errors":
                return row, 0
            changed = connection.execute(
                """
                UPDATE translation_batch_items
                SET status = 'pending', error_code = NULL,
                    error_summary = NULL, updated_at = ?
                WHERE batch_id = ? AND status = 'failed'
                """,
                (timestamp, batch_id),
            ).rowcount
            connection.execute(
                """
                UPDATE translation_batches
                SET status = CASE WHEN ? > 0 THEN 'queued' ELSE 'completed' END,
                    pause_reason = NULL, error_code = NULL,
                    error_summary = NULL, updated_at = ?
                WHERE batch_id = ?
                """,
                (changed, timestamp, batch_id),
            )
        return self.batch(batch_id), changed

    def close_failed_batch(self, batch_id: str) -> sqlite3.Row | None:
        self.database.execute(
            """
            UPDATE translation_batches
            SET status = 'cancelled', pause_reason = NULL,
                interactive_yielded = 0, updated_at = ?
            WHERE batch_id = ? AND status IN ('completed_with_errors', 'failed')
            """,
            (self._timestamp(), batch_id),
        )
        return self.batch(batch_id)

    def set_interactive_yielded(self, batch_id: str, yielded: bool) -> sqlite3.Row | None:
        self.database.execute(
            """
            UPDATE translation_batches
            SET interactive_yielded = ?, updated_at = ?
            WHERE batch_id = ? AND status NOT IN ('completed', 'cancelled')
            """,
            (int(yielded), self._timestamp(), batch_id),
        )
        return self.batch(batch_id)

    def set_batch_failed(
        self,
        batch_id: str,
        *,
        error_code: str,
        error_summary: str,
    ) -> sqlite3.Row | None:
        self.database.execute(
            """
            UPDATE translation_batches
            SET status = 'failed', interactive_yielded = 0,
                error_code = ?, error_summary = ?, updated_at = ?
            WHERE batch_id = ? AND status NOT IN ('completed', 'cancelled')
            """,
            (error_code, error_summary, self._timestamp(), batch_id),
        )
        return self.batch(batch_id)

    def settle_after_item(self, batch_id: str) -> sqlite3.Row | None:
        timestamp = self._timestamp()
        with self.database.transaction() as connection:
            row = connection.execute(
                "SELECT * FROM translation_batches WHERE batch_id = ?",
                (batch_id,),
            ).fetchone()
            if row is None:
                return None
            status = str(row["status"])
            if status == "pausing":
                connection.execute(
                    """
                    UPDATE translation_batches
                    SET status = 'paused', updated_at = ? WHERE batch_id = ?
                    """,
                    (timestamp, batch_id),
                )
            elif status == "cancelling":
                connection.execute(
                    """
                    UPDATE translation_batches
                    SET status = 'cancelled', interactive_yielded = 0,
                        updated_at = ? WHERE batch_id = ?
                    """,
                    (timestamp, batch_id),
                )
            elif status == "running":
                remaining = int(
                    connection.execute(
                        """
                        SELECT COUNT(*) FROM translation_batch_items
                        WHERE batch_id = ? AND status IN ('pending', 'running')
                        """,
                        (batch_id,),
                    ).fetchone()[0]
                )
                if remaining == 0:
                    failed = int(
                        connection.execute(
                            """
                            SELECT COUNT(*) FROM translation_batch_items
                            WHERE batch_id = ? AND status = 'failed'
                            """,
                            (batch_id,),
                        ).fetchone()[0]
                    )
                    connection.execute(
                        """
                        UPDATE translation_batches
                        SET status = ?, interactive_yielded = 0, updated_at = ?
                        WHERE batch_id = ?
                        """,
                        (
                            "completed_with_errors" if failed else "completed",
                            timestamp,
                            batch_id,
                        ),
                    )
        return self.batch(batch_id)

    def summary(
        self,
        batch_id: str,
        *,
        current_task: TranslationTaskState | None = None,
    ) -> TranslationBatchSummary | None:
        row = self.database.fetchone(
            """
            SELECT batches.*,
                COUNT(items.batch_item_id) total_chapters,
                COALESCE(SUM(CASE WHEN items.status = 'pending' THEN 1 ELSE 0 END), 0)
                    pending_chapters,
                COALESCE(SUM(CASE WHEN items.status = 'running' THEN 1 ELSE 0 END), 0)
                    running_chapters,
                COALESCE(SUM(CASE WHEN items.status = 'completed' THEN 1 ELSE 0 END), 0)
                    completed_chapters,
                COALESCE(SUM(CASE WHEN items.status = 'skipped' THEN 1 ELSE 0 END), 0)
                    skipped_chapters,
                COALESCE(SUM(CASE WHEN items.status = 'failed' THEN 1 ELSE 0 END), 0)
                    failed_chapters,
                COALESCE(SUM(CASE WHEN items.status = 'cancelled' THEN 1 ELSE 0 END), 0)
                    cancelled_chapters
            FROM translation_batches batches
            LEFT JOIN translation_batch_items items ON items.batch_id = batches.batch_id
            WHERE batches.batch_id = ?
            GROUP BY batches.batch_id
            """,
            (batch_id,),
        )
        if row is None:
            return None
        current = self.current_item(batch_id)
        return TranslationBatchSummary(
            batch_id=str(row["batch_id"]),
            comic_id=str(row["comic_id"]),
            comic_title=str(row["comic_title"]),
            status=str(row["status"]),
            pause_reason=(str(row["pause_reason"]) if row["pause_reason"] else None),
            interactive_yielded=bool(row["interactive_yielded"]),
            error_code=(str(row["error_code"]) if row["error_code"] else None),
            error_summary=(str(row["error_summary"]) if row["error_summary"] else None),
            total_chapters=int(row["total_chapters"]),
            pending_chapters=int(row["pending_chapters"]),
            running_chapters=int(row["running_chapters"]),
            completed_chapters=int(row["completed_chapters"]),
            skipped_chapters=int(row["skipped_chapters"]),
            failed_chapters=int(row["failed_chapters"]),
            cancelled_chapters=int(row["cancelled_chapters"]),
            current_item=self._item_summary(current) if current is not None else None,
            current_task=current_task,
            created_at=int(row["created_at"]),
            updated_at=int(row["updated_at"]),
        )

    def background_batch_ids(self) -> list[str]:
        rows = self.database.fetchall(
            """
            SELECT batch_id FROM translation_batches
            WHERE status NOT IN ('completed', 'cancelled')
            ORDER BY created_at ASC, rowid ASC
            """
        )
        return [str(row["batch_id"]) for row in rows]

    @staticmethod
    def _item_summary(row: sqlite3.Row) -> TranslationBatchItemSummary:
        return TranslationBatchItemSummary(
            batch_item_id=str(row["batch_item_id"]),
            chapter_id=str(row["chapter_id"]),
            chapter_title=str(row["chapter_title"]),
            position=int(row["position"]),
            status=str(row["status"]),
            attempts=int(row["attempts"]),
            error_code=(str(row["error_code"]) if row["error_code"] else None),
            error_summary=(str(row["error_summary"]) if row["error_summary"] else None),
        )

    @staticmethod
    def _timestamp() -> int:
        return int(time.time())
