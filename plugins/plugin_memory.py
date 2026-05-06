"""
plugins/plugin_memory.py

Stores and retrieves plugin performance history.
Bridges the plugin system with the existing memory subsystem:
  - episodic_memory for structured failure/success records
  - vector_store for semantic retrieval of similar past plugin executions

Used by:
  - plugin_health_monitor (to read performance history)
  - plugin_evolver (to understand why a plugin is degrading)
  - capability_gap_detector (to avoid re-generating an already-failed plugin)
"""
from __future__ import annotations

import json
import logging
import os
import sqlite3
import threading
import time
from datetime import datetime
from typing import Any

logger = logging.getLogger("PluginMemory")

PLUGIN_MEMORY_DB = os.path.join("memory", "stores", "plugin_memory.db")

_CREATE_RUNS_TABLE = """
CREATE TABLE IF NOT EXISTS plugin_runs (
    id           INTEGER PRIMARY KEY AUTOINCREMENT,
    plugin_name  TEXT    NOT NULL,
    intent       TEXT,
    status       TEXT    NOT NULL CHECK(status IN ('success','error','timeout')),
    elapsed_ms   INTEGER DEFAULT 0,
    error        TEXT,
    timestamp    REAL    NOT NULL
)
"""

_CREATE_EVOLUTION_TABLE = """
CREATE TABLE IF NOT EXISTS plugin_evolutions (
    id           INTEGER PRIMARY KEY AUTOINCREMENT,
    plugin_name  TEXT    NOT NULL,
    from_version TEXT,
    to_version   TEXT,
    reason       TEXT,
    timestamp    REAL    NOT NULL
)
"""

_CREATE_INDEXES = [
    "CREATE INDEX IF NOT EXISTS idx_runs_name      ON plugin_runs(plugin_name)",
    "CREATE INDEX IF NOT EXISTS idx_runs_timestamp ON plugin_runs(timestamp)",
    "CREATE INDEX IF NOT EXISTS idx_runs_status    ON plugin_runs(status)",
]


class PluginMemory:
    """
    Persists and retrieves plugin execution history.
    Also integrates with vector_store for semantic similarity lookups.
    """

    def __init__(self):
        self._db_path = PLUGIN_MEMORY_DB
        self._conn: sqlite3.Connection | None = None
        self._lock = threading.RLock()

    async def start(self):
        """Initialize the plugin memory database."""
        os.makedirs(os.path.dirname(self._db_path), exist_ok=True)
        self._init_db()
        logger.info("🧠 Plugin Memory: Online.")

    def _init_db(self):
        self._conn = sqlite3.connect(self._db_path, check_same_thread=False)
        self._conn.row_factory = sqlite3.Row
        cur = self._conn.cursor()
        cur.execute(_CREATE_RUNS_TABLE)
        cur.execute(_CREATE_EVOLUTION_TABLE)
        for idx in _CREATE_INDEXES:
            cur.execute(idx)
        self._conn.commit()

    # ── Write API ─────────────────────────────────────────────────────────────

    def record_run(
        self,
        plugin_name: str,
        intent: str,
        status: str,
        elapsed_ms: int = 0,
        error: str | None = None,
    ):
        """Record a single plugin execution result."""
        with self._lock:
            self._conn.execute(
                """INSERT INTO plugin_runs
                   (plugin_name, intent, status, elapsed_ms, error, timestamp)
                   VALUES (?,?,?,?,?,?)""",
                (plugin_name, intent, status, elapsed_ms, error, time.time()),
            )
            self._conn.commit()

    def record_evolution(
        self,
        plugin_name: str,
        from_version: str,
        to_version: str,
        reason: str,
    ):
        """Log a plugin version evolution event."""
        with self._lock:
            self._conn.execute(
                """INSERT INTO plugin_evolutions
                   (plugin_name, from_version, to_version, reason, timestamp)
                   VALUES (?,?,?,?,?)""",
                (plugin_name, from_version, to_version, reason, time.time()),
            )
            self._conn.commit()
        logger.info(
            f"📈 Evolution recorded: '{plugin_name}' {from_version} → {to_version}"
        )

    # ── Read API ──────────────────────────────────────────────────────────────

    def get_performance_summary(self, plugin_name: str, last_n: int = 20) -> dict:
        """
        Returns a summary of the last N runs for a plugin.
        Used by plugin_health_monitor and plugin_evolver.
        """
        with self._lock:
            cur = self._conn.execute(
                """SELECT status, elapsed_ms, error, timestamp
                   FROM plugin_runs
                   WHERE plugin_name = ?
                   ORDER BY timestamp DESC
                   LIMIT ?""",
                (plugin_name, last_n),
            )
            rows = [dict(r) for r in cur.fetchall()]

        if not rows:
            return {
                "plugin_name": plugin_name,
                "total_runs": 0,
                "success_rate": 0.0,
                "avg_elapsed_ms": 0,
                "recent_errors": [],
                "consecutive_failures": 0,
            }

        total = len(rows)
        successes = sum(1 for r in rows if r["status"] == "success")
        errors = [r["error"] for r in rows if r["error"]]
        avg_elapsed = int(sum(r["elapsed_ms"] for r in rows) / total)

        # Consecutive failures from most recent
        consecutive = 0
        for row in rows:
            if row["status"] != "success":
                consecutive += 1
            else:
                break

        return {
            "plugin_name": plugin_name,
            "total_runs": total,
            "success_rate": round(successes / total, 3),
            "avg_elapsed_ms": avg_elapsed,
            "recent_errors": list(set(errors))[:5],
            "consecutive_failures": consecutive,
        }

    def get_failure_rate_window(self, plugin_name: str, hours: int = 24) -> float:
        """Returns failure rate within a rolling time window."""
        cutoff = time.time() - (hours * 3600)
        with self._lock:
            cur = self._conn.execute(
                """SELECT COUNT(*) as total,
                          SUM(CASE WHEN status != 'success' THEN 1 ELSE 0 END) as failures
                   FROM plugin_runs
                   WHERE plugin_name = ? AND timestamp >= ?""",
                (plugin_name, cutoff),
            )
            row = cur.fetchone()

        if not row or row["total"] == 0:
            return 0.0
        return round(row["failures"] / row["total"], 3)

    def already_attempted(self, intent: str) -> bool:
        """
        Check if a plugin was already generated for this intent but failed.
        Prevents the gap detector from triggering the generator in an infinite loop.
        """
        with self._lock:
            cur = self._conn.execute(
                """SELECT COUNT(*) as cnt FROM plugin_runs
                   WHERE intent = ? AND status != 'success'""",
                (intent,),
            )
            row = cur.fetchone()
        return (row["cnt"] if row else 0) >= 3

    def get_evolution_history(self, plugin_name: str) -> list[dict]:
        """Returns the full evolution history for a plugin."""
        with self._lock:
            cur = self._conn.execute(
                """SELECT * FROM plugin_evolutions
                   WHERE plugin_name = ?
                   ORDER BY timestamp ASC""",
                (plugin_name,),
            )
            return [dict(r) for r in cur.fetchall()]

    def get_all_plugin_names(self) -> list[str]:
        """Returns list of all plugin names that have been run."""
        with self._lock:
            cur = self._conn.execute(
                "SELECT DISTINCT plugin_name FROM plugin_runs"
            )
            return [row["plugin_name"] for row in cur.fetchall()]

    # ── Vector Store Integration ───────────────────────────────────────────────

    async def save_to_vector_store(self, plugin_name: str, description: str, intent: str):
        """
        Saves plugin description to the vector store for semantic retrieval.
        Allows finding similar past plugins before generating a new one.
        """
        try:
            from memory.vector_store import vector_store
            if vector_store.collection:
                doc_id = f"plugin_{plugin_name}"
                content = f"Plugin: {plugin_name}. Intent: {intent}. {description}"
                vector_store.collection.upsert(
                    documents=[content],
                    metadatas=[{"plugin_name": plugin_name, "intent": intent}],
                    ids=[doc_id],
                )
                logger.debug(f"Plugin '{plugin_name}' indexed in vector store.")
        except Exception as e:
            logger.warning(f"Could not index plugin in vector store: {e}")

    async def find_similar_plugin(self, intent: str, min_similarity: float = 0.85) -> dict | None:
        """
        Searches vector store for a past plugin that handled a similar intent.
        Returns metadata dict only if the match is above min_similarity threshold.

        IMPORTANT: ChromaDB returns distances not similarities. A distance of 0
        means identical; lower is better. We convert: similarity = 1 - distance.
        We also check that the matched plugin actually has a manifest on disk —
        stale vector entries from cleaned-up failed runs must not be returned.
        """
        try:
            from memory.vector_store import vector_store
            if not vector_store.collection:
                return None
            results = vector_store.collection.query(
                query_texts=[intent], n_results=1,
                include=["metadatas", "distances"],
            )
            if not (results and results["metadatas"] and results["metadatas"][0]):
                return None

            meta      = results["metadatas"][0][0]
            distances = results.get("distances", [[]])[0]
            distance  = distances[0] if distances else 1.0

            # ChromaDB cosine distance: 0 = identical, 2 = opposite
            # Convert to similarity in [0, 1]
            similarity = max(0.0, 1.0 - (distance / 2.0))

            plugin_name = meta.get("plugin_name", "")
            if not plugin_name:
                return None

            if similarity < min_similarity:
                logger.debug(
                    f"Vector match '{plugin_name}' for '{intent}' "
                    f"below threshold (similarity={similarity:.2f} < {min_similarity}). "
                    f"Treating as new intent."
                )
                return None

            # Verify manifest exists on disk — don't return stale entries
            from core.config import settings
            plugins_dir = os.path.join(
                str(getattr(settings, "PLUGINS_DIR", "plugins")), "installed"
            )
            manifest_path = os.path.join(plugins_dir, plugin_name, "manifest.json")
            if not os.path.exists(manifest_path):
                logger.debug(
                    f"Vector store hit '{plugin_name}' has no manifest on disk — "
                    f"stale entry from a cleaned-up failed run. Ignoring."
                )
                return None

            logger.debug(
                f"Found similar plugin '{plugin_name}' for intent '{intent}' "
                f"(similarity={similarity:.2f})"
            )
            return meta

        except Exception as e:
            logger.warning(f"Vector similarity search failed: {e}")
        return None


# Global instance
plugin_memory = PluginMemory()