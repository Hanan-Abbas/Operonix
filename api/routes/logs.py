"""
api/routes/logs.py
───────────────────
Log streaming, querying, and management endpoints.

Changes from original
──────────────────────
All original endpoints are preserved verbatim:
  GET  /api/logs/recent   — fetch N most recent entries (ring buffer → file)
  GET  /api/logs/files    — list available log files
  GET  /api/logs/stream   — SSE real-time tail
  DELETE /api/logs/clear  — truncate file or clear ring buffer
  GET  /api/logs/levels   — count entries by level

Plan Phase 3 addition — routing decision structured log subscriber
───────────────────────────────────────────────────────────────────
The plan (§3.3) mandates:
  "Emit a structured routing_decision log entry per execution:
   { intent, chosen_method, confidence, rejected, outcome, duration_ms }"

Implemented as:

  RoutingDecisionLogger
    Subscribes to two events on the EventBus at module import time:
      • "routing_decision"   — fired by MethodRouter after every select()
      • "routing_mismatch"   — fired by executor on ROUTING_MISMATCH failure

    Each event is written into a dedicated in-memory ring buffer
    (routing_ring_buffer, maxsize from settings.ROUTING_LOG_MAXSIZE,
    default 500) and appended to a JSONL file in the log directory
    (routing_decisions.jsonl — no hardcoding, path from settings).

  GET /api/logs/routing
    New endpoint — returns recent routing decision entries with optional
    filtering by intent, method, and outcome.

  GET /api/logs/routing/stats
    Aggregated stats: method selection frequency, mismatch rate per method,
    average confidence per method — all computed from the ring buffer so
    no database is needed.

No existing endpoint signatures or behaviours are changed.
"""
from __future__ import annotations

import asyncio
import json
import logging
import os
import time
from datetime import datetime, timezone
from typing import Any, Dict, Generator, List, Optional

from fastapi import APIRouter, HTTPException, Query
from fastapi.responses import StreamingResponse

logger = logging.getLogger("LogsRoute")

router = APIRouter(prefix="/api/logs", tags=["logs"])


# ─────────────────────────────────────────────────────────────────────────────
# Config helpers (unchanged — no hardcoding)
# ─────────────────────────────────────────────────────────────────────────────

def _log_dir() -> str:
    try:
        from core.config import settings
        return str(getattr(settings, "LOG_DIR", None) or
                   getattr(settings, "LOGS_DIR", None) or "logs")
    except Exception:
        return "logs"


def _log_files() -> Dict[str, str]:
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
    files = _log_files()
    if not files:
        return None
    return max(files.values(), key=lambda p: os.path.getmtime(p))


def _routing_log_path() -> str:
    """Path for the routing decisions JSONL file — from settings, no hardcoding."""
    try:
        from core.config import settings
        return str(
            getattr(settings, "ROUTING_LOG_PATH", None)
            or os.path.join(_log_dir(), "routing_decisions.jsonl")
        )
    except Exception:
        return os.path.join(_log_dir(), "routing_decisions.jsonl")


def _routing_ring_maxsize() -> int:
    try:
        from core.config import settings
        return int(getattr(settings, "ROUTING_LOG_MAXSIZE", 500))
    except Exception:
        return 500


# ─────────────────────────────────────────────────────────────────────────────
# Ring buffer (unchanged from original)
# ─────────────────────────────────────────────────────────────────────────────

class _RingBuffer:
    def __init__(self, maxsize: int = 2000) -> None:
        self._buf : List[Dict] = []
        self._max : int        = maxsize

    def append(self, entry: Dict) -> None:
        if len(self._buf) >= self._max:
            self._buf.pop(0)
        self._buf.append(entry)

    def tail(
        self,
        n      : int,
        level  : Optional[str] = None,
        source : Optional[str] = None,
    ) -> List[Dict]:
        entries = self._buf
        if level:
            entries = [
                e for e in entries
                if e.get("level", "").upper() == level.upper()
            ]
        if source:
            entries = [e for e in entries if e.get("source", "") == source]
        return entries[-n:]

    def clear(self) -> None:
        self._buf.clear()

    def __len__(self) -> int:
        return len(self._buf)


ring_buffer = _RingBuffer()   # core.logger calls ring_buffer.append(entry)

# Phase 3: dedicated ring buffer for routing decisions
routing_ring_buffer = _RingBuffer(maxsize=_routing_ring_maxsize())


# ─────────────────────────────────────────────────────────────────────────────
# Phase 3 — RoutingDecisionLogger
# ─────────────────────────────────────────────────────────────────────────────

class RoutingDecisionLogger:
    """
    Subscribes to routing events on the EventBus and persists them.

    Subscriptions
    ─────────────
    routing_decision  — fired by MethodRouter.select() after every call.
      Payload (from MethodRouter._emit_routing_event):
        { intent, chosen_method, confidence, fallback_chain,
          rejected, duration_ms }

    routing_mismatch  — fired by executor._handle_failure() on
      FailureClass.ROUTING_MISMATCH.
      Payload (from event_bus._build_routing_mismatch_payload):
        { task_id, step_index, intent, method, failure_class,
          detail, fallback_chain, decision_log, summary }

    Each entry is stored in routing_ring_buffer and appended to
    routing_decisions.jsonl.  outcome is inferred:
      "routing_decision" → outcome = "dispatched"
      "routing_mismatch" → outcome = "mismatch"
    """

    def __init__(self) -> None:
        self._logger = logging.getLogger("RoutingDecisionLogger")

    def start(self) -> None:
        """
        Subscribe to routing events.  Call once at app startup — after the
        event bus is initialised — so subscriptions are registered before
        any task is dispatched.
        """
        try:
            from core.event_bus import bus as _bus
            _bus.subscribe("routing_decision", self._on_routing_decision)
            _bus.subscribe("routing_mismatch",  self._on_routing_mismatch)
            self._logger.info(
                "RoutingDecisionLogger: subscribed to routing_decision "
                "and routing_mismatch."
            )
        except Exception as exc:
            self._logger.warning(
                "RoutingDecisionLogger: could not subscribe to event bus: %s", exc
            )

    # ── Event handlers ────────────────────────────────────────────────────────

    async def _on_routing_decision(self, event: Any) -> None:
        """
        Handles "routing_decision" — emitted by MethodRouter after every
        select() call, regardless of outcome.

        Structured log entry schema (Phase 3 plan requirement):
          { intent, chosen_method, confidence, rejected, outcome,
            duration_ms, fallback_chain, timestamp }
        """
        try:
            data = event.data or {}
            entry: Dict[str, Any] = {
                "event"         : "routing_decision",
                "timestamp"     : datetime.now(timezone.utc).isoformat(),
                "intent"        : data.get("intent", "unknown"),
                "chosen_method" : data.get("chosen_method", "unknown"),
                "confidence"    : round(float(data.get("confidence", 0.0)), 4),
                "fallback_chain": data.get("fallback_chain", []),
                "rejected"      : data.get("rejected", []),
                "duration_ms"   : data.get("duration_ms", 0),
                "outcome"       : "dispatched",
            }
            self._persist(entry)
        except Exception as exc:
            self._logger.warning(
                "_on_routing_decision failed (non-fatal): %s", exc
            )

    async def _on_routing_mismatch(self, event: Any) -> None:
        """
        Handles "routing_mismatch" — emitted by executor on
        FailureClass.ROUTING_MISMATCH.

        Structured log entry schema:
          { intent, chosen_method, confidence, rejected, outcome,
            duration_ms, fallback_chain, task_id, step_index,
            detail, timestamp }

        outcome is "mismatch" so callers can filter dispatched vs failed.
        """
        try:
            data = event.data or {}
            decision_log: dict = data.get("decision_log") or {}
            entry: Dict[str, Any] = {
                "event"         : "routing_mismatch",
                "timestamp"     : datetime.now(timezone.utc).isoformat(),
                "task_id"       : data.get("task_id", "unknown"),
                "step_index"    : data.get("step_index", -1),
                "intent"        : data.get("intent", "unknown"),
                "chosen_method" : data.get("method", "unknown"),
                "confidence"    : round(
                    float(decision_log.get("confidence", 0.0)), 4
                ),
                "fallback_chain": data.get("fallback_chain", []),
                "rejected"      : decision_log.get("rejected", []),
                "detail"        : data.get("detail", ""),
                "duration_ms"   : 0,   # not available at failure time
                "outcome"       : "mismatch",
            }
            self._persist(entry)
        except Exception as exc:
            self._logger.warning(
                "_on_routing_mismatch failed (non-fatal): %s", exc
            )

    def _persist(self, entry: Dict[str, Any]) -> None:
        """
        Write *entry* to the in-memory ring buffer and append it to the
        JSONL file on disk.  Both writes are best-effort — a failure in
        either never propagates to the caller.
        """
        routing_ring_buffer.append(entry)

        log_path = _routing_log_path()
        try:
            os.makedirs(os.path.dirname(log_path), exist_ok=True)
            with open(log_path, "a", encoding="utf-8") as fh:
                fh.write(json.dumps(entry) + "\n")
        except Exception as exc:
            self._logger.debug(
                "Could not write routing log to %s: %s", log_path, exc
            )


# Instantiate and wire up at import time — the FastAPI app calls
# routing_decision_logger.start() in its lifespan handler.
routing_decision_logger = RoutingDecisionLogger()


# ─────────────────────────────────────────────────────────────────────────────
# Helpers (unchanged from original)
# ─────────────────────────────────────────────────────────────────────────────

def _parse_line(raw: str) -> Optional[Dict]:
    raw = raw.strip()
    if not raw:
        return None
    try:
        return json.loads(raw)
    except json.JSONDecodeError:
        return {
            "timestamp" : datetime.now(timezone.utc).isoformat(),
            "level"     : "INFO",
            "message"   : raw,
            "source"    : "unknown",
        }


def _tail_file(path: str, n: int) -> List[Dict]:
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
# Original endpoints (unchanged)
# ─────────────────────────────────────────────────────────────────────────────

@router.get("/recent")
async def get_recent_logs(
    limit  : int           = Query(default=100, ge=1, le=2000),
    level  : Optional[str] = Query(default=None),
    source : Optional[str] = Query(default=None),
    log    : Optional[str] = Query(default=None),
) -> Dict[str, Any]:
    """Fetch the N most recent log entries (ring buffer → file fallback)."""
    if not log and len(ring_buffer) > 0:
        entries = ring_buffer.tail(limit, level=level, source=source)
        return {"source": "ring_buffer", "count": len(entries), "entries": entries}

    files = _log_files()
    if log:
        path = files.get(log)
        if not path:
            raise HTTPException(
                status_code=404,
                detail=f"Log '{log}' not found. Available: {list(files.keys())}",
            )
    else:
        path = _default_log_path()

    if not path:
        return {
            "source": "none", "count": 0, "entries": [],
            "message": "No log files found.",
        }

    entries = _tail_file(path, limit * 3)
    if level:
        entries = [
            e for e in entries if e.get("level", "").upper() == level.upper()
        ]
    if source:
        entries = [e for e in entries if e.get("source", "") == source]
    entries = entries[-limit:]

    return {
        "source": os.path.basename(path),
        "count": len(entries),
        "entries": entries,
    }


@router.get("/files")
async def list_log_files() -> Dict[str, Any]:
    """List all available log files."""
    files  = _log_files()
    result = []
    for name, path in files.items():
        stat = os.stat(path) if os.path.exists(path) else None
        result.append({
            "name"       : name,
            "path"       : path,
            "size_bytes" : stat.st_size if stat else 0,
            "modified_at": (
                datetime.fromtimestamp(
                    stat.st_mtime, tz=timezone.utc
                ).isoformat()
                if stat else None
            ),
        })
    return {"files": result, "count": len(result)}


@router.get("/stream")
async def stream_logs(
    log     : Optional[str] = Query(default=None),
    level   : Optional[str] = Query(default=None),
    source  : Optional[str] = Query(default=None),
    poll_ms : int            = Query(default=500, ge=100, le=5000),
) -> StreamingResponse:
    """Stream logs in real-time using Server-Sent Events."""
    files = _log_files()
    if log:
        path = files.get(log)
        if not path:
            raise HTTPException(
                status_code=404, detail=f"Log '{log}' not found."
            )
    else:
        path = _default_log_path()

    async def _sse_generator() -> Generator[str, None, None]:
        last_pos = 0
        if path and os.path.exists(path):
            last_pos = os.path.getsize(path)

        while True:
            if not path and len(ring_buffer) > 0:
                entries = ring_buffer.tail(20, level=level, source=source)
                for entry in entries:
                    yield f"data: {json.dumps(entry)}\n\n"
                await asyncio.sleep(poll_ms / 1000)
                continue

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

            yield f": heartbeat {time.time()}\n\n"
            await asyncio.sleep(poll_ms / 1000)

    return StreamingResponse(
        _sse_generator(),
        media_type="text/event-stream",
        headers={"Cache-Control": "no-cache", "X-Accel-Buffering": "no"},
    )


@router.delete("/clear")
async def clear_logs(
    log: Optional[str] = Query(default=None),
) -> Dict[str, Any]:
    """Clear a log file or the in-memory ring buffer."""
    if log:
        files = _log_files()
        path  = files.get(log)
        if not path:
            raise HTTPException(
                status_code=404, detail=f"Log '{log}' not found."
            )
        try:
            open(path, "w").close()
            return {"status": "success", "cleared": path}
        except Exception as exc:
            raise HTTPException(status_code=500, detail=str(exc))
    else:
        ring_buffer.clear()
        return {"status": "success", "cleared": "ring_buffer"}


@router.get("/levels")
async def log_level_summary() -> Dict[str, Any]:
    """Count log entries by level from the ring buffer."""
    counts: Dict[str, int] = {}
    for entry in ring_buffer._buf:
        lvl = entry.get("level", "UNKNOWN").upper()
        counts[lvl] = counts.get(lvl, 0) + 1
    return {"counts": counts, "total": len(ring_buffer)}


# ─────────────────────────────────────────────────────────────────────────────
# Phase 3 — new routing endpoints
# ─────────────────────────────────────────────────────────────────────────────

@router.get("/routing")
async def get_routing_logs(
    limit   : int           = Query(default=100, ge=1, le=2000),
    intent  : Optional[str] = Query(default=None, description="Filter by intent"),
    method  : Optional[str] = Query(default=None, description="Filter by chosen_method"),
    outcome : Optional[str] = Query(
        default=None,
        description="Filter by outcome: 'dispatched' | 'mismatch'",
    ),
) -> Dict[str, Any]:
    """
    Fetch recent routing decision entries.

    Each entry follows the Phase 3 schema:
      { event, timestamp, intent, chosen_method, confidence,
        fallback_chain, rejected, duration_ms, outcome, [task_id, detail] }

    Falls back to routing_decisions.jsonl when the ring buffer is empty
    (e.g. after a server restart).
    """
    entries: List[Dict] = []

    if len(routing_ring_buffer) > 0:
        entries = list(routing_ring_buffer._buf)
        source_label = "routing_ring_buffer"
    else:
        path = _routing_log_path()
        entries = _tail_file(path, limit * 3)
        source_label = os.path.basename(path) if os.path.exists(path) else "none"

    # Apply filters
    if intent:
        entries = [e for e in entries if e.get("intent", "") == intent]
    if method:
        entries = [
            e for e in entries if e.get("chosen_method", "") == method
        ]
    if outcome:
        entries = [e for e in entries if e.get("outcome", "") == outcome]

    entries = entries[-limit:]

    return {
        "source"  : source_label,
        "count"   : len(entries),
        "entries" : entries,
    }


@router.get("/routing/stats")
async def get_routing_stats() -> Dict[str, Any]:
    """
    Aggregated routing statistics computed from the in-memory ring buffer.

    Returns:
      method_frequency   — how often each method was chosen
      mismatch_rate      — mismatches / total per method (0.0 – 1.0)
      avg_confidence     — mean confidence score per method
      top_intents        — most frequently routed intents (top 10)
      total_decisions    — total routing decisions recorded
      total_mismatches   — total routing_mismatch events recorded
    """
    entries = list(routing_ring_buffer._buf)
    if not entries:
        return {
            "method_frequency" : {},
            "mismatch_rate"    : {},
            "avg_confidence"   : {},
            "top_intents"      : [],
            "total_decisions"  : 0,
            "total_mismatches" : 0,
        }

    method_total    : Dict[str, int]         = {}
    method_mismatch : Dict[str, int]         = {}
    method_conf_sum : Dict[str, float]       = {}
    intent_count    : Dict[str, int]         = {}

    for entry in entries:
        m       = entry.get("chosen_method", "unknown")
        outcome = entry.get("outcome", "dispatched")
        conf    = float(entry.get("confidence", 0.0))
        intent  = entry.get("intent", "unknown")

        method_total[m]    = method_total.get(m, 0) + 1
        method_conf_sum[m] = method_conf_sum.get(m, 0.0) + conf
        intent_count[intent] = intent_count.get(intent, 0) + 1

        if outcome == "mismatch":
            method_mismatch[m] = method_mismatch.get(m, 0) + 1

    mismatch_rate: Dict[str, float] = {
        m: round(method_mismatch.get(m, 0) / method_total[m], 4)
        for m in method_total
    }
    avg_confidence: Dict[str, float] = {
        m: round(method_conf_sum[m] / method_total[m], 4)
        for m in method_total
    }
    top_intents = sorted(
        intent_count.items(), key=lambda kv: -kv[1]
    )[:10]

    total_mismatches = sum(
        1 for e in entries if e.get("outcome") == "mismatch"
    )

    return {
        "method_frequency" : method_total,
        "mismatch_rate"    : mismatch_rate,
        "avg_confidence"   : avg_confidence,
        "top_intents"      : [
            {"intent": i, "count": c} for i, c in top_intents
        ],
        "total_decisions"  : len(entries),
        "total_mismatches" : total_mismatches,
    }