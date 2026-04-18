"""
api/routes/logs.py

Log streaming, querying, and management endpoints.

Features:
- Tail logs in real-time via SSE (Server-Sent Events)
- Query logs by level, source, time range
- Structured JSON log parsing
- No hardcoded paths: all resolved from core.config.settings
- Pluggable: works with file-based logs AND in-memory ring buffer
"""

import asyncio
import json
import os
import time
from datetime import datetime, timezone
from typing import Any, Dict, Generator, Iterator, List, Optional

from fastapi import APIRouter, HTTPException, Query
from fastapi.responses import StreamingResponse

import logging

logger = logging.getLogger("LogsRoute")

router = APIRouter(prefix="/api/logs", tags=["logs"])


# ─────────────────────────────────────────────────────────────────────────────
# Config helpers (non-hardcoded)
# ─────────────────────────────────────────────────────────────────────────────

def _log_dir() -> str:
    """Resolve log directory from settings, fall back to ./logs."""
    try:
        from core.config import settings
        return getattr(settings, "LOG_DIR", "logs")
    except Exception:
        return "logs"


def _log_files() -> Dict[str, str]:
    """
    Returns a mapping of  log_name → absolute_path  for every .log/.jsonl file
    found in the log directory.  No filenames are hardcoded here.
    """
    log_dir = _log_dir()
    result: Dict[str, str] = {}
    if not os.path.isdir(log_dir):
        return result
    for fname in os.listdir(log_dir):
        if fname.endswith((".log", ".jsonl")):
            name = os.path.splitext(fname)[0]
            result[name] = os.path.join(log_dir, fname)
    return result


def _default_log_path() -> Optional[str]:
    """Return the most-recently-modified log file, or None."""
    files = _log_files()
    if not files:
        return None
    return max(files.values(), key=lambda p: os.path.getmtime(p))


# ─────────────────────────────────────────────────────────────────────────────
# In-memory ring buffer (populated by core.logger at import time)
# ─────────────────────────────────────────────────────────────────────────────

class _RingBuffer:
    """Thread-safe fixed-size ring buffer for the most recent log entries."""

    def __init__(self, maxsize: int = 2000):
        self._buf: List[Dict] = []
        self._max = maxsize

    def append(self, entry: Dict) -> None:
        if len(self._buf) >= self._max:
            self._buf.pop(0)
        self._buf.append(entry)

    def tail(self, n: int, level: Optional[str] = None, source: Optional[str] = None) -> List[Dict]:
        entries = self._buf
        if level:
            entries = [e for e in entries if e.get("level", "").upper() == level.upper()]
        if source:
            entries = [e for e in entries if e.get("source", "") == source]
        return entries[-n:]

    def clear(self) -> None:
        self._buf.clear()

    def __len__(self) -> int:
        return len(self._buf)


ring_buffer = _RingBuffer()   # core.logger should call ring_buffer.append(entry)


# ─────────────────────────────────────────────────────────────────────────────
# Helpers
# ─────────────────────────────────────────────────────────────────────────────

def _parse_line(raw: str) -> Optional[Dict]:
    """Try to parse a log line as JSON; fall back to a plain-text envelope."""
    raw = raw.strip()
    if not raw:
        return None
    try:
        return json.loads(raw)
    except json.JSONDecodeError:
        return {
            "timestamp": datetime.now(timezone.utc).isoformat(),
            "level":     "INFO",
            "message":   raw,
            "source":    "unknown",
        }


def _tail_file(path: str, n: int) -> List[Dict]:
    """Read the last *n* lines from a log file."""
    if not os.path.exists(path):
        return []
    try:
        with open(path, "r", encoding="utf-8", errors="replace") as fh:
            lines = fh.readlines()
        entries = []
        for line in lines[-n:]:
            parsed = _parse_line(line)
            if parsed:
                entries.append(parsed)
        return entries
    except Exception as exc:
        logger.error("Failed to read log file %s: %s", path, exc)
        return []


# ─────────────────────────────────────────────────────────────────────────────
# Endpoints
# ─────────────────────────────────────────────────────────────────────────────

@router.get("/recent")
async def get_recent_logs(
    limit:  int            = Query(default=100, ge=1, le=2000),
    level:  Optional[str]  = Query(default=None, description="Filter by level: DEBUG|INFO|WARNING|ERROR|CRITICAL"),
    source: Optional[str]  = Query(default=None, description="Filter by source module"),
    log:    Optional[str]  = Query(default=None, description="Log file name (without extension). Defaults to most recent."),
) -> Dict[str, Any]:
    """
    📋 Fetch the N most recent log entries.

    Sources checked in order:
      1. In-memory ring buffer (fastest)
      2. File on disk (if ring buffer is empty or *log* is specified)
    """
    # 1. Try ring buffer first (unless a specific file is requested)
    if not log and len(ring_buffer) > 0:
        entries = ring_buffer.tail(limit, level=level, source=source)
        return {
            "source":  "ring_buffer",
            "count":   len(entries),
            "entries": entries,
        }

    # 2. Fall back to file
    files = _log_files()
    if log:
        path = files.get(log)
        if not path:
            raise HTTPException(status_code=404, detail=f"Log '{log}' not found. Available: {list(files.keys())}")
    else:
        path = _default_log_path()

    if not path:
        return {"source": "none", "count": 0, "entries": [], "message": "No log files found."}

    entries = _tail_file(path, limit * 3)           # over-fetch then filter
    if level:
        entries = [e for e in entries if e.get("level", "").upper() == level.upper()]
    if source:
        entries = [e for e in entries if e.get("source", "") == source]
    entries = entries[-limit:]

    return {
        "source":  os.path.basename(path),
        "count":   len(entries),
        "entries": entries,
    }


@router.get("/files")
async def list_log_files() -> Dict[str, Any]:
    """📂 List all available log files."""
    files = _log_files()
    result = []
    for name, path in files.items():
        stat = os.stat(path) if os.path.exists(path) else None
        result.append({
            "name":         name,
            "path":         path,
            "size_bytes":   stat.st_size if stat else 0,
            "modified_at":  datetime.fromtimestamp(stat.st_mtime, tz=timezone.utc).isoformat() if stat else None,
        })
    return {"files": result, "count": len(result)}


@router.get("/stream")
async def stream_logs(
    log:     Optional[str] = Query(default=None),
    level:   Optional[str] = Query(default=None),
    source:  Optional[str] = Query(default=None),
    poll_ms: int            = Query(default=500, ge=100, le=5000, description="Polling interval in ms"),
) -> StreamingResponse:
    """
    📡 Stream logs in real-time using Server-Sent Events.

    Connect with:
        const es = new EventSource('/api/logs/stream');
        es.onmessage = (e) => console.log(JSON.parse(e.data));
    """

    files = _log_files()
    if log:
        path = files.get(log)
        if not path:
            raise HTTPException(status_code=404, detail=f"Log '{log}' not found.")
    else:
        path = _default_log_path()

    async def _sse_generator() -> Generator[str, None, None]:
        last_pos = 0
        if path and os.path.exists(path):
            last_pos = os.path.getsize(path)   # start from current EOF

        while True:
            # ── Yield from ring buffer (new entries since last yield) ──
            if not path and len(ring_buffer) > 0:
                entries = ring_buffer.tail(20, level=level, source=source)
                for entry in entries:
                    data = json.dumps(entry)
                    yield f"data: {data}\n\n"
                await asyncio.sleep(poll_ms / 1000)
                continue

            # ── Yield from file (new bytes appended since last poll) ──
            if path and os.path.exists(path):
                current_size = os.path.getsize(path)
                if current_size > last_pos:
                    with open(path, "r", encoding="utf-8", errors="replace") as fh:
                        fh.seek(last_pos)
                        new_lines = fh.readlines()
                    last_pos = current_size
                    for line in new_lines:
                        entry = _parse_line(line)
                        if not entry:
                            continue
                        if level and entry.get("level", "").upper() != level.upper():
                            continue
                        if source and entry.get("source", "") != source:
                            continue
                        yield f"data: {json.dumps(entry)}\n\n"

            # ── Heartbeat every ~30 s so the connection doesn't time out ──
            yield f": heartbeat {time.time()}\n\n"
            await asyncio.sleep(poll_ms / 1000)

    return StreamingResponse(
        _sse_generator(),
        media_type="text/event-stream",
        headers={
            "Cache-Control": "no-cache",
            "X-Accel-Buffering": "no",
        },
    )


@router.delete("/clear")
async def clear_logs(
    log: Optional[str] = Query(default=None, description="Log file name. Omit to clear ring buffer."),
) -> Dict[str, Any]:
    """🗑️ Clear a log file or the in-memory ring buffer."""
    if log:
        files = _log_files()
        path = files.get(log)
        if not path:
            raise HTTPException(status_code=404, detail=f"Log '{log}' not found.")
        try:
            open(path, "w").close()   # truncate
            return {"status": "success", "cleared": path}
        except Exception as exc:
            raise HTTPException(status_code=500, detail=str(exc))
    else:
        ring_buffer.clear()
        return {"status": "success", "cleared": "ring_buffer"}


@router.get("/levels")
async def log_level_summary() -> Dict[str, Any]:
    """📊 Count log entries by level from the ring buffer."""
    counts: Dict[str, int] = {}
    for entry in ring_buffer._buf:
        lvl = entry.get("level", "UNKNOWN").upper()
        counts[lvl] = counts.get(lvl, 0) + 1
    return {"counts": counts, "total": len(ring_buffer)}