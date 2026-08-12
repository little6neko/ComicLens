from __future__ import annotations

import json
import sqlite3
import time

from app.domain.comic import ChapterSummary, ComicSummary
from app.domain.library import FavoriteItem, HistoryItem, HistoryUpdate
from app.repositories.database import Database


class LibraryRepository:
    def __init__(self, database: Database) -> None:
        self.database = database

    def save_favorite(
        self, comic_id: str, summary: ComicSummary, cover_source_url: str
    ) -> FavoriteItem:
        timestamp = self._timestamp()
        self.database.execute(
            """
            INSERT INTO favorites(
                comic_id, title, cover_source_url, rating, is_adult,
                latest_chapters_json, favorited_at
            ) VALUES (?, ?, ?, ?, ?, ?, ?)
            ON CONFLICT(comic_id) DO UPDATE SET
                title = excluded.title,
                cover_source_url = excluded.cover_source_url,
                rating = excluded.rating,
                is_adult = excluded.is_adult,
                latest_chapters_json = excluded.latest_chapters_json
            """,
            self._snapshot_parameters(comic_id, summary, cover_source_url, timestamp),
        )
        row = self.database.fetchone("SELECT * FROM favorites WHERE comic_id = ?", (comic_id,))
        assert row is not None
        return self._favorite_from_row(row)

    def list_favorites(self) -> list[FavoriteItem]:
        return [
            self._favorite_from_row(row)
            for row in self.database.fetchall(
                "SELECT * FROM favorites ORDER BY favorited_at DESC, comic_id"
            )
        ]

    def delete_favorite(self, comic_id: str) -> bool:
        return bool(self.database.execute("DELETE FROM favorites WHERE comic_id = ?", (comic_id,)))

    def clear_favorites(self) -> int:
        return self.database.execute("DELETE FROM favorites")

    def save_history(
        self, comic_id: str, payload: HistoryUpdate, cover_source_url: str
    ) -> HistoryItem:
        timestamp = self._timestamp()
        summary = ComicSummary(
            comic_id=comic_id,
            title=payload.title,
            cover_url=cover_source_url,
            rating=payload.rating,
            is_adult=payload.is_adult,
            latest_chapters=payload.latest_chapters,
        )
        snapshot = self._snapshot_parameters(comic_id, summary, cover_source_url, timestamp)
        self.database.execute(
            """
            INSERT INTO reading_history(
                comic_id, title, cover_source_url, rating, is_adult,
                latest_chapters_json, chapter_id, chapter_title,
                page_index, total_pages, updated_at
            ) VALUES (?, ?, ?, ?, ?, ?, ?, ?, ?, ?, ?)
            ON CONFLICT(comic_id) DO UPDATE SET
                title = excluded.title,
                cover_source_url = excluded.cover_source_url,
                rating = excluded.rating,
                is_adult = excluded.is_adult,
                latest_chapters_json = excluded.latest_chapters_json,
                chapter_id = excluded.chapter_id,
                chapter_title = excluded.chapter_title,
                page_index = excluded.page_index,
                total_pages = excluded.total_pages,
                updated_at = excluded.updated_at
            """,
            (
                *snapshot[:-1],
                payload.chapter_id,
                payload.chapter_title,
                payload.page_index,
                payload.total_pages,
                timestamp,
            ),
        )
        row = self.database.fetchone(
            "SELECT * FROM reading_history WHERE comic_id = ?", (comic_id,)
        )
        assert row is not None
        return self._history_from_row(row)

    def list_history(self) -> list[HistoryItem]:
        return [
            self._history_from_row(row)
            for row in self.database.fetchall(
                "SELECT * FROM reading_history ORDER BY updated_at DESC, comic_id"
            )
        ]

    def delete_history(self, comic_id: str) -> bool:
        return bool(
            self.database.execute("DELETE FROM reading_history WHERE comic_id = ?", (comic_id,))
        )

    def clear_history(self) -> int:
        return self.database.execute("DELETE FROM reading_history")

    def set_chapter_read(self, comic_id: str, chapter_id: str, read: bool) -> None:
        if read:
            self.database.execute(
                """
                INSERT INTO read_chapters(comic_id, chapter_id, read_at)
                VALUES (?, ?, ?)
                ON CONFLICT(comic_id, chapter_id) DO UPDATE SET
                    read_at = excluded.read_at
                """,
                (comic_id, chapter_id, self._timestamp()),
            )
        else:
            self.database.execute(
                "DELETE FROM read_chapters WHERE comic_id = ? AND chapter_id = ?",
                (comic_id, chapter_id),
            )

    def read_chapters(self, comic_id: str) -> list[str]:
        return [
            str(row["chapter_id"])
            for row in self.database.fetchall(
                """
                SELECT chapter_id FROM read_chapters
                WHERE comic_id = ? ORDER BY read_at DESC, chapter_id
                """,
                (comic_id,),
            )
        ]

    @staticmethod
    def _snapshot_parameters(
        comic_id: str,
        summary: ComicSummary,
        cover_source_url: str,
        timestamp: int,
    ) -> tuple[object, ...]:
        return (
            comic_id,
            summary.title,
            cover_source_url,
            summary.rating,
            int(summary.is_adult),
            json.dumps(
                [item.model_dump(by_alias=True) for item in summary.latest_chapters],
                ensure_ascii=False,
                separators=(",", ":"),
            ),
            timestamp,
        )

    def _favorite_from_row(self, row: sqlite3.Row) -> FavoriteItem:
        return FavoriteItem(
            comic=self._summary_from_row(row),
            favorited_at=int(row["favorited_at"]),
        )

    def _history_from_row(self, row: sqlite3.Row) -> HistoryItem:
        return HistoryItem(
            comic=self._summary_from_row(row),
            chapter_id=str(row["chapter_id"]),
            chapter_title=str(row["chapter_title"]),
            page_index=int(row["page_index"]),
            total_pages=int(row["total_pages"]),
            updated_at=int(row["updated_at"]),
        )

    @staticmethod
    def _summary_from_row(row: sqlite3.Row) -> ComicSummary:
        chapters = [
            ChapterSummary.model_validate(value)
            for value in json.loads(str(row["latest_chapters_json"]))
        ]
        return ComicSummary(
            comic_id=str(row["comic_id"]),
            title=str(row["title"]),
            cover_url=str(row["cover_source_url"]),
            rating=(float(row["rating"]) if row["rating"] is not None else None),
            is_adult=bool(row["is_adult"]),
            latest_chapters=chapters,
        )

    @staticmethod
    def _timestamp() -> int:
        return time.time_ns() // 1_000_000
