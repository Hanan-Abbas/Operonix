"""
api/routes/actions.py

Action history endpoints.

The action log is a newline-delimited JSON file (one entry per line).
Each entry is written by executor/execution_tracker.py when a task completes.

No filenames or schemas are hardcoded: the log path comes from settings and
the entry schema is whatever the tracker writes — we just pass it through.
"""

import json
import logging
import os
from datetime import datetime, timezone
from typing import Any, Dict, List, Optional

from fastapi import APIRouter, HTTPException, Query

logger = logging.getLogger("ActionsRoute")

router = APIRouter(prefix="/api/actions", tags=["actions"])


# ─────────────────────────────────────────────────────────────────────────────
# Config helper
# ─────────────────────────────────────────────────────────────────────────────

def _log_path() -> str:
    """Resolve the actions log path from settings without hardcoding."""
    try:
        from core.config import settings
        log_dir = getattr(settings, "LOG_DIR", "logs")
        log_name = getattr(settings, "ACTIONS_LOG_FILE", "actions.log")
        return os.path.join(log_dir, log_name)
    except Exception:
        return os.path.join("logs", "actions.log")


# ─────────────────────────────────────────────────────────────────────────────
# Helpers
# ─────────────────────────────────────────────────────────────────────────────

def _read_entries(
    path:   str,
    limit:  int,
    offset: int,
    status: Optional[str],
    source: Optional[str],
) -> List[Dict]:
    """
    Read, optionally filter, and paginate entries from the action log.
    Returns newest-first.
    """
    if not os.path.exists(path):
        return []

    try:
        with open(path, "r", encoding="utf-8", errors="replace") as fh:
            lines = fh.readlines()
    except Exception as exc:
        logger.error("Failed to open action log %s: %s", path, exc)
        return []

    entries: List[Dict] = []
    for line in reversed(lines):
        line = line.strip()
        if not line:
            continue
        try:
            entry = json.loads(line)
        except json.JSONDecodeError:
            entry = {"raw": line, "timestamp": None, "status": "unknown"}
        entries.append(entry)

    # ── Filter ──────────────────────────────────────────────────────────────
    if status:
        entries = [e for e in entries if e.get("status", "").lower() == status.lower()]
    if source:
        entries = [e for e in entries if e.get("source", "") == source]

    # ── Paginate ─────────────────────────────────────────────────────────────
    return entries[offset: offset + limit]


# ─────────────────────────────────────────────────────────────────────────────
# Endpoints
# ─────────────────────────────────────────────────────────────────────────────

@router.get("/history")
async def get_action_history(
    limit:  int           = Query(default=50,  ge=1,  le=1000),
    offset: int           = Query(default=0,   ge=0),
    status: Optional[str] = Query(default=None, description="Filter by status: success|failed|partial"),
    source: Optional[str] = Query(default=None, description="Filter by originating component"),
) -> Dict[str, Any]:
    """
    📋 Return paginated action history (newest first).

    Query params:
      limit  – max entries to return (1–1000, default 50)
      offset – pagination offset
      status – optional status filter
      source – optional source component filter
    """
    path    = _log_path()
    entries = _read_entries(path, limit=limit, offset=offset, status=status, source=source)
    return {
        "actions": entries,
        "count":   len(entries),
        "offset":  offset,
        "limit":   limit,
        "log":     path,
    }


@router.get("/summary")
async def get_action_summary() -> Dict[str, Any]:
    """
    📊 Aggregate action counts by status and source.

    Reads the entire log file — use sparingly on large logs.
    """
    path = _log_path()
    if not os.path.exists(path):
        return {"total": 0, "by_status": {}, "by_source": {}}

    by_status: Dict[str, int] = {}
    by_source: Dict[str, int] = {}
    total = 0

    try:
        with open(path, "r", encoding="utf-8", errors="replace") as fh:
            for line in fh:
                line = line.strip()
                if not line:
                    continue
                total += 1
                try:
                    entry = json.loads(line)
                except json.JSONDecodeError:
                    continue
                st  = entry.get("status", "unknown")
                src = entry.get("source", "unknown")
                by_status[st]  = by_status.get(st, 0) + 1
                by_source[src] = by_source.get(src, 0) + 1

    except Exception as exc:
        logger.error("Summary failed: %s", exc)
        raise HTTPException(status_code=500, detail=str(exc))

    return {"total": total, "by_status": by_status, "by_source": by_source}


@router.get("/{task_id}")
async def get_action_by_id(task_id: str) -> Dict[str, Any]:
    """🔍 Look up a single action entry by task_id."""
    path = _log_path()
    if not os.path.exists(path):
        raise HTTPException(status_code=404, detail="Action log does not exist.")

    try:
        with open(path, "r", encoding="utf-8", errors="replace") as fh:
            for line in fh:
                line = line.strip()
                if not line:
                    continue
                try:
                    entry = json.loads(line)
                    if entry.get("task_id") == task_id:
                        return entry
                except json.JSONDecodeError:
                    continue
    except Exception as exc:
        raise HTTPException(status_code=500, detail=str(exc))

    raise HTTPException(status_code=404, detail=f"Action '{task_id}' not found.")


@router.delete("/clear")
async def clear_action_history() -> Dict[str, Any]:
    """🗑️ Truncate the action log file."""
    path = _log_path()
    try:
        if os.path.exists(path):
            open(path, "w").close()
        return {"status": "success", "message": "Action history cleared.", "log": path}
    except Exception as exc:
        raise HTTPException(status_code=500, detail=str(exc))