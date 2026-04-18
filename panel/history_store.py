"""
panel/history_store.py

Persistent command history backed by SQLite (aiosqlite).
Schema is append-only; nothing is ever deleted except by pruning
when the limit is reached.

This store is shared — the learning module reads from it too.
Location: ~/.operonix/panel_history.db
"""
from __future__ import annotations

import asyncio
import logging
from dataclasses import dataclass
from datetime import datetime
from pathlib import Path
from typing import Any

try:
    import aiosqlite
    _HAS_AIOSQLITE = True
except ImportError:
    _HAS_AIOSQLITE = False

log = logging.getLogger(__name__)

_DB_PATH = Path.home() / ".operonix" / "panel_history.db"

_DDL = """
CREATE TABLE IF NOT EXISTS commands (
    id              INTEGER PRIMARY KEY AUTOINCREMENT,
    timestamp       TEXT    NOT NULL,
    query_text      TEXT    NOT NULL,
    intent_resolved TEXT,
    method_used     TEXT,
    success         INTEGER NOT NULL DEFAULT 0,
    duration_ms     INTEGER,
    app_context     TEXT
);
CREATE INDEX IF NOT EXISTS idx_commands_ts ON commands (timestamp DESC);
"""


@dataclass
class HistoryEntry:
    id: int
    timestamp: str
    query_text: str
    intent_resolved: str | None
    method_used: str | None
    success: bool
    duration_ms: int | None
    app_context: str | None


class HistoryStore:
    """Async SQLite-backed command history."""

    def __init__(self, db_path: Path | None = None) -> None:
        self._path = db_path or _DB_PATH
        self._conn: Any = None

    async def open(self) -> None:
        if not _HAS_AIOSQLITE:
            log.warning("history_store: aiosqlite not installed — history disabled.")
            return
        self._path.parent.mkdir(parents=True, exist_ok=True)
        self._conn = await aiosqlite.connect(str(self._path))
        self._conn.row_factory = aiosqlite.Row
        await self._conn.executescript(_DDL)
        await self._conn.commit()
        log.info("history_store: opened %s", self._path)

    async def close(self) -> None:
        if self._conn:
            await self._conn.close()

    async def record(
        self,
        query_text: str,
        intent_resolved: str | None = None,
        method_used: str | None = None,
        success: bool = False,
        duration_ms: int | None = None,
        app_context: str | None = None,
        limit: int = 200,
    ) -> int | None:
        """Insert a new entry and prune old ones over *limit*. Returns new row id."""
        if not self._conn:
            return None
        ts = datetime.utcnow().isoformat()
        try:
            cursor = await self._conn.execute(
                """
                INSERT INTO commands
                    (timestamp, query_text, intent_resolved, method_used, success, duration_ms, app_context)
                VALUES (?, ?, ?, ?, ?, ?, ?)
                """,
                (ts, query_text, intent_resolved, method_used, int(success), duration_ms, app_context),
            )
            row_id = cursor.lastrowid
            # Prune entries beyond the limit.
            await self._conn.execute(
                """
                DELETE FROM commands WHERE id NOT IN (
                    SELECT id FROM commands ORDER BY id DESC LIMIT ?
                )
                """,
                (limit,),
            )
            await self._conn.commit()
            return row_id
        except Exception as exc:  # noqa: BLE001
            log.error("history_store: record failed — %s", exc)
            return None

    async def update_outcome(
        self,
        row_id: int,
        success: bool,
        duration_ms: int,
        intent_resolved: str | None = None,
        method_used: str | None = None,
    ) -> None:
        """Update an existing row after execution completes."""
        if not self._conn or row_id is None:
            return
        try:
            await self._conn.execute(
                """
                UPDATE commands
                SET success=?, duration_ms=?, intent_resolved=?, method_used=?
                WHERE id=?
                """,
                (int(success), duration_ms, intent_resolved, method_used, row_id),
            )
            await self._conn.commit()
        except Exception as exc:  # noqa: BLE001
            log.error("history_store: update_outcome failed — %s", exc)

    async def fetch(self, limit: int = 200, offset: int = 0) -> list[HistoryEntry]:
        """Fetch recent history entries, newest first."""
        if not self._conn:
            return []
        try:
            async with self._conn.execute(
                """
                SELECT id, timestamp, query_text, intent_resolved,
                       method_used, success, duration_ms, app_context
                FROM commands
                ORDER BY id DESC
                LIMIT ? OFFSET ?
                """,
                (limit, offset),
            ) as cursor:
                rows = await cursor.fetchall()
            return [
                HistoryEntry(
                    id=row["id"],
                    timestamp=row["timestamp"],
                    query_text=row["query_text"],
                    intent_resolved=row["intent_resolved"],
                    method_used=row["method_used"],
                    success=bool(row["success"]),
                    duration_ms=row["duration_ms"],
                    app_context=row["app_context"],
                )
                for row in rows
            ]
        except Exception as exc:  # noqa: BLE001
            log.error("history_store: fetch failed — %s", exc)
            return []

    async def search(self, query: str, limit: int = 50) -> list[HistoryEntry]:
        """Full-text search over query_text."""
        if not self._conn:
            return []
        pattern = f"%{query}%"
        try:
            async with self._conn.execute(
                """
                SELECT id, timestamp, query_text, intent_resolved,
                       method_used, success, duration_ms, app_context
                FROM commands
                WHERE query_text LIKE ?
                ORDER BY id DESC
                LIMIT ?
                """,
                (pattern, limit),
            ) as cursor:
                rows = await cursor.fetchall()
            return [
                HistoryEntry(
                    id=row["id"],
                    timestamp=row["timestamp"],
                    query_text=row["query_text"],
                    intent_resolved=row["intent_resolved"],
                    method_used=row["method_used"],
                    success=bool(row["success"]),
                    duration_ms=row["duration_ms"],
                    app_context=row["app_context"],
                )
                for row in rows
            ]
        except Exception as exc:  # noqa: BLE001
            log.error("history_store: search failed — %s", exc)
            return []