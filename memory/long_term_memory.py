from __future__ import annotations

import json
import logging
import os
import sqlite3
import threading
import time
from typing import Any

from core.config import settings
from core.event_bus import bus


class LongTermMemory:
    """📦 Long-Term Memory for the AI OS.

    Listens for archived tasks from SessionMemory and flushes them to persistent
    disk storage. Provides search/retrieval for the learning and planning
    systems.

    REFLECTOR INTEGRATION:
    Added get_float() / set_float() / get_kv() / set_kv() for the Reflector
    and Planner to persist and read per-capability confidence scores and any
    other scalar/structured values that must survive process restarts.

    These are backed by a lightweight SQLite KV table (kv_store) in the same
    stores directory as task_history.jsonl — no new dependencies required.

    RISK MITIGATIONS:
      R1 — All KV methods are wrapped in try/except and return safe defaults
           on failure so neither the Reflector nor the Planner can be blocked
           by a disk/DB error.
      R2 — SQLite connection uses check_same_thread=False with a threading
           RLock so get_float/set_float are safe from both the async event
           loop and any background threads.
      R3 — KV table is created lazily in _ensure_kv_db() which is called at
           the top of every get/set method, so callers that instantiate
           LongTermMemory before start() (e.g. Planner._best_method_from_reflector)
           still get a working store without needing the event loop.
      R4 — float values are clamped and validated on read so a corrupted DB
           value never causes a math error downstream.
    """

    def __init__(self):
        self.logger = logging.getLogger("LongTermMemory")

        # Define the file path where successful tasks are permanently stored
        self.storage_dir = os.path.join("memory", "stores")
        self.history_file = os.path.join(self.storage_dir, "task_history.jsonl")

        # KV store for Reflector confidence scores and other scalar values (R2)
        self._kv_db_path = os.path.join(self.storage_dir, "kv_store.db")
        self._kv_conn: sqlite3.Connection | None = None
        self._kv_lock = threading.RLock()

    async def start(self):
        """Ensure storage directories exist and subscribe to the archive

        event.
        """
        # Create the storage folder if it doesn't exist
        os.makedirs(self.storage_dir, exist_ok=True)

        # Listen to SessionMemory when it finishes archiving a successful task
        bus.subscribe("task_memory_archived", self.save_task_to_disk)

        self.logger.info(
            f"📦 Long-Term Memory: Online. Persisting to {self.history_file}"
        )

    async def save_task_to_disk(self, event):
        """Appends a completed task snapshot to a JSONL file on disk."""
        task_data = event.data
        task_id = task_data.get("task_id")

        # FIX: guard against None task_id (was caused by session_memory not
        # injecting task_id into its payload dict — fixed there, guarded here).
        if not task_id or not isinstance(task_id, str):
            self.logger.warning(
                "save_task_to_disk: skipping — invalid task_id %r", task_id
            )
            return

        if task_data.get("status") != "completed":
            self.logger.debug(
                f"Skipping long-term storage for task {task_id} (Status: {task_data.get('status')})"
            )
            return

        record = {
            "task_id":     task_id,
            "timestamp":   time.time(),
            "intent":      task_data.get("intent", "unknown"),
            "steps_count": len(task_data.get("steps", [])),
            "data":        task_data,
        }

        try:
            with open(self.history_file, "a", encoding="utf-8") as f:
                f.write(json.dumps(record) + "\n")

            self.logger.info(
                f"💾 Long-Term Memory: Successfully persisted task [{task_id}] to disk."
            )

            bus.publish(
                "long_term_memory_updated",
                {"task_id": task_id},
                source="long_term_memory",
            )

        except OSError as e:
            self.logger.error(
                f"Failed to write task {task_id} to long-term storage: {e}"
            )

    def search_past_tasks(self, intent: str, limit: int = 5):
        """Searches the local JSONL file for past tasks that match a specific

        intent.

        (Used by the planner to see if we already know how to do something!)
        """
        if not os.path.exists(self.history_file):
            return []

        results = []
        try:
            with open(self.history_file, "r", encoding="utf-8") as f:
                for line in f:
                    record = json.loads(line)
                    task_data = record.get("data", {})

                    # If this past task matches what we are looking for
                    if task_data.get("intent") == intent:
                        results.append(task_data)

                    if len(results) >= limit:
                        break
        except Exception as e:
            self.logger.error(f"Error searching long-term memory: {e}")

        return results

    # ── KV Store (Reflector confidence scores + any scalar values) ────────────

    def _ensure_kv_db(self) -> bool:
        """
        Lazily initialises the SQLite KV store.

        Called at the top of every get/set method so LongTermMemory instances
        created before start() (e.g. in Planner) work correctly (R3).

        Returns True if the connection is ready, False on error (R1).
        """
        if self._kv_conn is not None:
            return True
        try:
            os.makedirs(self.storage_dir, exist_ok=True)
            self._kv_conn = sqlite3.connect(
                self._kv_db_path, check_same_thread=False   # R2
            )
            self._kv_conn.execute(
                """CREATE TABLE IF NOT EXISTS kv_store (
                    key        TEXT PRIMARY KEY,
                    value_json TEXT NOT NULL,
                    updated_at REAL NOT NULL
                )"""
            )
            self._kv_conn.commit()
            return True
        except Exception as exc:
            self.logger.warning("LongTermMemory: KV DB init failed: %s", exc)
            return False

    def get_float(self, key: str, default: float = 0.75) -> float:
        """
        Read a float value from the KV store.

        Used by the Planner's _best_method_from_reflector() and by the
        Reflector's get_capability_confidence() to read persisted scores.

        Parameters
        ----------
        key     : e.g. "confidence:plugin", "confidence:api"
        default : returned when key is absent or DB is unavailable (R1, R4)

        RISK R4 — value is validated as float and clamped to [0.0, 1.0] so
        a corrupted DB row can never cause a ZeroDivisionError or overflow
        downstream.
        """
        try:
            if not self._ensure_kv_db():
                return default
            with self._kv_lock:
                cur = self._kv_conn.execute(
                    "SELECT value_json FROM kv_store WHERE key = ?", (key,)
                )
                row = cur.fetchone()
            if row is None:
                return default
            raw = json.loads(row[0])
            # R4 — clamp and validate
            val = float(raw)
            return max(0.0, min(1.0, val))
        except Exception as exc:
            self.logger.debug("get_float('%s') failed (non-fatal): %s", key, exc)
            return default   # R1

    def set_float(self, key: str, value: float) -> None:
        """
        Persist a float value into the KV store (upsert).

        Called by Reflector._update_confidence() after every task reflection
        to adjust the per-tier capability confidence score.

        RISK R1 — exceptions are caught and logged at DEBUG; the Reflector's
        own try/except in _update_confidence() provides a second safety net.
        RISK R4 — value is clamped before storage to preserve the 0.0–1.0
        invariant even if the caller passes an out-of-range number.
        """
        try:
            if not self._ensure_kv_db():
                return
            clamped = max(0.0, min(1.0, float(value)))  # R4
            with self._kv_lock:
                self._kv_conn.execute(
                    """INSERT INTO kv_store (key, value_json, updated_at)
                       VALUES (?, ?, ?)
                       ON CONFLICT(key) DO UPDATE SET
                           value_json = excluded.value_json,
                           updated_at = excluded.updated_at""",
                    (key, json.dumps(clamped), time.time()),
                )
                self._kv_conn.commit()
            self.logger.debug("set_float('%s') = %.4f", key, clamped)
        except Exception as exc:
            self.logger.debug("set_float('%s') failed (non-fatal): %s", key, exc)  # R1

    def get_kv(self, key: str, default: Any = None) -> Any:
        """
        Read an arbitrary JSON-serialisable value from the KV store.

        General-purpose companion to get_float() — used for storing structured
        data such as Lesson dicts or pattern summaries.

        RISK R1 — returns default on any error, never raises.
        """
        try:
            if not self._ensure_kv_db():
                return default
            with self._kv_lock:
                cur = self._kv_conn.execute(
                    "SELECT value_json FROM kv_store WHERE key = ?", (key,)
                )
                row = cur.fetchone()
            return json.loads(row[0]) if row else default
        except Exception as exc:
            self.logger.debug("get_kv('%s') failed (non-fatal): %s", key, exc)
            return default  # R1

    def set_kv(self, key: str, value: Any) -> None:
        """
        Persist an arbitrary JSON-serialisable value into the KV store (upsert).

        RISK R1 — exceptions are caught and logged at DEBUG; never raises.
        """
        try:
            if not self._ensure_kv_db():
                return
            with self._kv_lock:
                self._kv_conn.execute(
                    """INSERT INTO kv_store (key, value_json, updated_at)
                       VALUES (?, ?, ?)
                       ON CONFLICT(key) DO UPDATE SET
                           value_json = excluded.value_json,
                           updated_at = excluded.updated_at""",
                    (key, json.dumps(value), time.time()),
                )
                self._kv_conn.commit()
        except Exception as exc:
            self.logger.debug("set_kv('%s') failed (non-fatal): %s", key, exc)  # R1


# Global instance
long_term_memory = LongTermMemory()