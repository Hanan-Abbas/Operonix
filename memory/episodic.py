"""
memory/episodic.py

Hybrid episodic memory system:

1. Working Memory (in-process dict)
   - Stores active task chain for the current session
   - Cleared after task completes

2. Episodic Storage (SQLite, persistent)
   - Logs every task with intent, actions, outcome, timestamps
   - Tracks failure history per intent for gap detection

3. Failure Tracking
   - Counts consecutive failures per intent
   - Counts failures within a rolling time window
   - Published to event bus for capability_gap_detector to consume

4. Semantic retrieval (via vector_store integration)
   - Similar past episodes can be retrieved by embedding similarity
"""
from __future__ import annotations

import asyncio
import json
import logging
import os
import sqlite3
import threading
import time
from datetime import datetime, timedelta
from typing import Any

from core.config import settings
from core.event_bus import bus

logger = logging.getLogger("EpisodicMemory")

DB_PATH = os.path.join("memory", "stores", "episodic.db")

_CREATE_EPISODES_TABLE = """
CREATE TABLE IF NOT EXISTS episodes (
    id          INTEGER PRIMARY KEY AUTOINCREMENT,
    task_id     TEXT    NOT NULL,
    intent      TEXT    NOT NULL,
    actions     TEXT,           -- JSON array of action strings
    outcome     TEXT    NOT NULL CHECK(outcome IN ('success','failure')),
    failure_reason TEXT,
    attempts    INTEGER DEFAULT 1,
    created_at  REAL    NOT NULL,  -- Unix timestamp
    metadata    TEXT            -- JSON blob for extras
)
"""

_CREATE_FAILURES_TABLE = """
CREATE TABLE IF NOT EXISTS intent_failures (
    id              INTEGER PRIMARY KEY AUTOINCREMENT,
    intent          TEXT    NOT NULL,
    failure_reason  TEXT,
    attempts        INTEGER DEFAULT 1,
    timestamp       REAL    NOT NULL
)
"""

_CREATE_INDEXES = [
    "CREATE INDEX IF NOT EXISTS idx_episodes_intent    ON episodes(intent)",
    "CREATE INDEX IF NOT EXISTS idx_episodes_outcome   ON episodes(outcome)",
    "CREATE INDEX IF NOT EXISTS idx_failures_intent    ON intent_failures(intent)",
    "CREATE INDEX IF NOT EXISTS idx_failures_timestamp ON intent_failures(timestamp)",
]


class EpisodicMemory:
    """
    Hybrid episodic memory: in-memory working store + SQLite persistence.
    """

    def __init__(self):
        self.logger = logging.getLogger("EpisodicMemory")
        # Working memory: task_id -> list of step dicts
        self._working: dict[str, list[dict]] = {}
        self._lock = threading.RLock()
        self._db_path = DB_PATH
        self._conn: sqlite3.Connection | None = None

    # ── Lifecycle ─────────────────────────────────────────────────────────────

    async def start(self):
        """Initialize the DB and subscribe to task events."""
        os.makedirs(os.path.dirname(self._db_path), exist_ok=True)
        self._init_db()

        bus.subscribe("task_completed", self._on_task_completed)
        bus.subscribe("task_failed",    self._on_task_failed)
        bus.subscribe("step_executed",  self._on_step_executed)

        self.logger.info("📖 Episodic Memory: Online (SQLite + working memory).")

    def _init_db(self):
        self._conn = sqlite3.connect(self._db_path, check_same_thread=False)
        self._conn.row_factory = sqlite3.Row
        cur = self._conn.cursor()
        cur.execute(_CREATE_EPISODES_TABLE)
        cur.execute(_CREATE_FAILURES_TABLE)
        for idx in _CREATE_INDEXES:
            cur.execute(idx)
        self._conn.commit()
        self.logger.info(f"💾 Episodic DB initialized at {self._db_path}")

    # ── Working Memory ────────────────────────────────────────────────────────

    def add_step(self, task_id: str, step: dict):
        """Append a step to working memory for an active task."""
        with self._lock:
            self._working.setdefault(task_id, []).append(step)

    def get_working_memory(self, task_id: str) -> list[dict]:
        """Return the current step chain for a task."""
        with self._lock:
            return list(self._working.get(task_id, []))

    def clear_working_memory(self, task_id: str):
        """Clears working memory for a task (call after archiving)."""
        with self._lock:
            self._working.pop(task_id, None)

    # ── Event Handlers ────────────────────────────────────────────────────────

    async def _on_step_executed(self, event):
        data = event.data or {}
        task_id = data.get("task_id")
        step = data.get("step", {})
        if task_id and step:
            self.add_step(task_id, step)

    async def _on_task_completed(self, event):
        data = event.data or {}
        task_id = data.get("task_id", "unknown")
        intent  = data.get("intent", "unknown")
        steps   = data.get("steps") or self.get_working_memory(task_id)

        await self.record_episode(
            task_id=task_id,
            intent=intent,
            steps=steps,
            outcome="success",
        )
        self.clear_working_memory(task_id)

    async def _on_task_failed(self, event):
        data = event.data or {}
        task_id = data.get("task_id", "unknown")
        intent  = data.get("intent", "unknown")
        reason  = data.get("error") or data.get("reason", "Unknown failure")
        steps   = data.get("steps") or self.get_working_memory(task_id)

        await self.record_episode(
            task_id=task_id,
            intent=intent,
            steps=steps,
            outcome="failure",
            failure_reason=reason,
        )
        await self._record_failure(intent, reason)
        self.clear_working_memory(task_id)

    # ── Core Persistence ──────────────────────────────────────────────────────

    async def record_episode(
        self,
        task_id: str,
        intent: str,
        steps: list,
        outcome: str,
        failure_reason: str | None = None,
        attempts: int = 1,
        metadata: dict | None = None,
    ):
        """Persist an episode to SQLite asynchronously."""
        actions_json = json.dumps(
            [s.get("action", "") for s in steps if isinstance(s, dict)]
        )
        meta_json = json.dumps(metadata or {})

        await asyncio.to_thread(
            self._insert_episode,
            task_id, intent, actions_json, outcome,
            failure_reason, attempts, meta_json,
        )
        self.logger.debug(
            f"📖 Episode recorded: task={task_id} intent={intent} outcome={outcome}"
        )

    def _insert_episode(
        self, task_id, intent, actions_json, outcome,
        failure_reason, attempts, meta_json,
    ):
        with self._lock:
            self._conn.execute(
                """INSERT INTO episodes
                   (task_id, intent, actions, outcome, failure_reason, attempts, created_at, metadata)
                   VALUES (?,?,?,?,?,?,?,?)""",
                (task_id, intent, actions_json, outcome,
                 failure_reason, attempts, time.time(), meta_json),
            )
            self._conn.commit()

    async def _record_failure(self, intent: str, reason: str, attempts: int = 1):
        """Log a failure entry for gap detection."""
        await asyncio.to_thread(
            self._insert_failure, intent, reason, attempts
        )

    def _insert_failure(self, intent: str, reason: str, attempts: int):
        with self._lock:
            self._conn.execute(
                """INSERT INTO intent_failures (intent, failure_reason, attempts, timestamp)
                   VALUES (?,?,?,?)""",
                (intent, reason, attempts, time.time()),
            )
            self._conn.commit()

    # ── Failure Query API (used by capability_gap_detector) ───────────────────

    def get_consecutive_failures(self, intent: str) -> int:
        """
        Returns the number of consecutive failures for an intent
        (reset whenever a success is recorded).
        """
        with self._lock:
            cur = self._conn.execute(
                """SELECT outcome FROM episodes
                   WHERE intent = ?
                   ORDER BY created_at DESC
                   LIMIT 10""",
                (intent,),
            )
            rows = cur.fetchall()

        consecutive = 0
        for row in rows:
            if row["outcome"] == "failure":
                consecutive += 1
            else:
                break  # Hit a success — streak broken
        return consecutive

    def get_failures_in_window(self, intent: str, hours: int = 24) -> int:
        """
        Returns failure count for an intent within the last N hours.
        """
        cutoff = time.time() - (hours * 3600)
        with self._lock:
            cur = self._conn.execute(
                """SELECT COUNT(*) as cnt FROM intent_failures
                   WHERE intent = ? AND timestamp >= ?""",
                (intent, cutoff),
            )
            row = cur.fetchone()
        return row["cnt"] if row else 0

    def get_all_recent_failures(self, hours: int = 24) -> list[dict]:
        """
        Returns all failure records within the last N hours.
        Used by gap detector to scan for new gaps without knowing the intent.
        """
        cutoff = time.time() - (hours * 3600)
        with self._lock:
            cur = self._conn.execute(
                """SELECT intent, failure_reason, attempts, timestamp
                   FROM intent_failures
                   WHERE timestamp >= ?
                   ORDER BY timestamp DESC""",
                (cutoff,),
            )
            rows = cur.fetchall()
        return [dict(r) for r in rows]

    # ── Retrieval API ──────────────────────────────────────────────────────────

    def get_recent_episodes(self, intent: str, limit: int = 5) -> list[dict]:
        """Fetch recent episodes for a given intent."""
        with self._lock:
            cur = self._conn.execute(
                """SELECT * FROM episodes WHERE intent = ?
                   ORDER BY created_at DESC LIMIT ?""",
                (intent, limit),
            )
            rows = cur.fetchall()
        return [dict(r) for r in rows]

    def get_failure_summary(self, intent: str) -> dict:
        """
        Returns a structured failure summary for a given intent.
        Used by the generator to build informed fix prompts.
        """
        recent = self.get_recent_episodes(intent, limit=10)
        failures = [e for e in recent if e["outcome"] == "failure"]
        reasons = list({f["failure_reason"] for f in failures if f["failure_reason"]})

        return {
            "intent": intent,
            "total_failures": len(failures),
            "consecutive_failures": self.get_consecutive_failures(intent),
            "window_failures_24h": self.get_failures_in_window(intent),
            "common_reasons": reasons[:5],
        }


# Global instance
episodic_memory = EpisodicMemory()