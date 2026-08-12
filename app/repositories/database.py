from __future__ import annotations

import sqlite3
import threading
import time
from collections.abc import Iterable, Iterator, Sequence
from contextlib import contextmanager
from pathlib import Path
from typing import Any


class Database:
    def __init__(self, path: Path) -> None:
        path.parent.mkdir(parents=True, exist_ok=True)
        self.path = path
        self._lock = threading.RLock()
        self._connection = sqlite3.connect(
            path,
            check_same_thread=False,
            isolation_level=None,
            timeout=10,
        )
        self._connection.row_factory = sqlite3.Row
        with self._lock:
            self._connection.execute("PRAGMA foreign_keys = ON")
            self._connection.execute("PRAGMA journal_mode = WAL")
            self._connection.execute("PRAGMA synchronous = NORMAL")
            self._connection.execute("PRAGMA busy_timeout = 10000")
        self._migrate()

    def close(self) -> None:
        with self._lock:
            self._connection.close()

    def execute(self, sql: str, parameters: Sequence[Any] = ()) -> int:
        with self._lock:
            cursor = self._connection.execute(sql, parameters)
            return cursor.rowcount

    def executemany(self, sql: str, parameters: Iterable[Sequence[Any]]) -> None:
        with self._lock:
            self._connection.executemany(sql, parameters)

    def fetchone(self, sql: str, parameters: Sequence[Any] = ()) -> sqlite3.Row | None:
        with self._lock:
            return self._connection.execute(sql, parameters).fetchone()

    def fetchall(self, sql: str, parameters: Sequence[Any] = ()) -> list[sqlite3.Row]:
        with self._lock:
            return list(self._connection.execute(sql, parameters).fetchall())

    def scalar(self, sql: str, parameters: Sequence[Any] = ()) -> Any:
        row = self.fetchone(sql, parameters)
        return row[0] if row is not None else None

    @contextmanager
    def transaction(self) -> Iterator[sqlite3.Connection]:
        with self._lock:
            self._connection.execute("BEGIN IMMEDIATE")
            try:
                yield self._connection
            except Exception:
                self._connection.rollback()
                raise
            else:
                self._connection.commit()

    def _migrate(self) -> None:
        migrations_dir = Path(__file__).parent / "migrations"
        migration_files = sorted(migrations_dir.glob("[0-9][0-9][0-9]_*.sql"))
        with self._lock:
            self._connection.execute(
                """
                CREATE TABLE IF NOT EXISTS schema_migrations (
                    version INTEGER PRIMARY KEY,
                    name TEXT NOT NULL,
                    applied_at INTEGER NOT NULL
                )
                """
            )
            applied = {
                int(row[0])
                for row in self._connection.execute(
                    "SELECT version FROM schema_migrations"
                ).fetchall()
            }
            for migration_path in migration_files:
                version = int(migration_path.name.split("_", 1)[0])
                if version in applied:
                    continue
                sql = migration_path.read_text(encoding="utf-8")
                escaped_name = migration_path.name.replace("'", "''")
                script = (
                    "BEGIN IMMEDIATE;\n"
                    f"{sql}\n"
                    "INSERT INTO schema_migrations(version, name, applied_at) "
                    f"VALUES ({version}, '{escaped_name}', {int(time.time())});\n"
                    "COMMIT;"
                )
                try:
                    self._connection.executescript(script)
                except Exception:
                    if self._connection.in_transaction:
                        self._connection.rollback()
                    raise
