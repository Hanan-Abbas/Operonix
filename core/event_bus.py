"""
core/event_bus.py
──────────────────
Event bus — extended with typed routing_mismatch event (Plan §3.3 / Phase 3).

Changes from original
──────────────────────
All original functionality is preserved verbatim:
  • Event class with dict-compat shims
  • subscribe(), emit(), publish(), run(), _execute_callback()
  • Priority queue (stop/abort/fail → 10, log/metric → 90, default 50)
  • Qt dead-object guard in _execute_callback()
  • Thread-safe publish() via call_soon_threadsafe()

Plan Phase 3 addition — typed routing_mismatch event
──────────────────────────────────────────────────────
The plan requires that when the executor tags a failure as
FailureClass.ROUTING_MISMATCH, a structured event is emitted with:
  • The full fallback chain (so the dashboard can show what was tried)
  • The MethodDecision log dict (intent, method, confidence, rejected)
  • The failure detail

This is implemented as:

  emit_routing_mismatch(data) -> coroutine
    Async helper that publishes a "routing_mismatch" event at priority 10
    (same as "fail" — high urgency) with the structured payload validated
    against ROUTING_MISMATCH_SCHEMA.  Missing keys are filled with safe
    defaults so malformed executor payloads never crash the bus.

  publish_routing_mismatch(data)
    Synchronous wrapper for emit_routing_mismatch(), callable from
    non-async executor paths via the standard thread-safe publish() route.

Both methods are also available on the module-level `bus` singleton so
callers import nothing extra:
    bus.publish_routing_mismatch({...})
    await bus.emit_routing_mismatch({...})

The executor (executor.py _handle_failure) already calls bus.publish()
with event type "routing_mismatch" — these helpers are additive.  They
add schema validation and the correct priority, but they are not required
for the event to flow; the existing publish() call is sufficient.
"""
from __future__ import annotations

import asyncio
import fnmatch
import logging
from datetime import datetime
from typing import Any, Callable, Dict, List, Optional

logger = logging.getLogger("EventBus")

# ─────────────────────────────────────────────────────────────────────────────
# Routing mismatch event schema — required keys with safe defaults
# ─────────────────────────────────────────────────────────────────────────────

_ROUTING_MISMATCH_DEFAULTS: dict[str, Any] = {
    "task_id"       : "unknown",
    "step_index"    : -1,
    "intent"        : "unknown",
    "method"        : "unknown",
    "failure_class" : "routing_mismatch",
    "detail"        : "",
    "fallback_chain": [],
    "decision_log"  : {},
}


def _build_routing_mismatch_payload(data: dict) -> dict:
    """
    Merge *data* with safe defaults so every routing_mismatch event has
    the same schema regardless of what the executor provided.

    Also injects a human-readable summary string for the dashboard's
    live_logs.js component which displays it directly.
    """
    payload = {**_ROUTING_MISMATCH_DEFAULTS, **data}

    # Human-readable summary for dashboard display (Phase 3 observability)
    payload.setdefault(
        "summary",
        (
            f"Routing mismatch: intent='{payload['intent']}' "
            f"method='{payload['method']}' — "
            f"{payload['detail'] or 'no detail'}"
        ),
    )

    # Normalise fallback_chain to a list of strings
    raw_chain = payload.get("fallback_chain") or []
    if raw_chain and not isinstance(raw_chain[0], str):
        payload["fallback_chain"] = [
            m.value if hasattr(m, "value") else str(m) for m in raw_chain
        ]

    return payload


# ─────────────────────────────────────────────────────────────────────────────
# Event
# ─────────────────────────────────────────────────────────────────────────────

class Event:

    def __init__(
        self,
        name   : str,
        data   : Any  = None,
        source : str  = "system",
    ) -> None:
        self.name      = name
        self.data      = data
        self.source    = source or "system"
        self.timestamp = datetime.now().isoformat()

    def __str__(self) -> str:
        return f"[{self.timestamp}] {self.source} -> {self.name}: {self.data}"

    def __lt__(self, other: "Event") -> bool:
        return self.timestamp < other.timestamp

    # ── Dict-compatibility shims ──────────────────────────────────────────────

    def get(self, key: str, default: Any = None) -> Any:
        if isinstance(self.data, dict):
            return self.data.get(key, default)
        return default

    def __getitem__(self, key: str) -> Any:
        if isinstance(self.data, dict):
            return self.data[key]
        raise KeyError(key)

    def __contains__(self, key: str) -> bool:
        if isinstance(self.data, dict):
            return key in self.data
        return False


# ─────────────────────────────────────────────────────────────────────────────
# EventBus
# ─────────────────────────────────────────────────────────────────────────────

class EventBus:

    # Priority constants — lower number = processed first
    _PRIORITY_HIGH    : int = 10   # stop, abort, fail, security, routing_mismatch
    _PRIORITY_DEFAULT : int = 50
    _PRIORITY_LOW     : int = 90   # log, metric, update, state

    def __init__(self) -> None:
        self.listeners   : Dict[str, List[Callable]] = {}
        self.logger      = logging.getLogger("EventBus")
        self._event_loop : Optional[asyncio.AbstractEventLoop] = None
        self._queue      = asyncio.PriorityQueue()

    # ── Subscription ──────────────────────────────────────────────────────────

    def subscribe(self, event_pattern: str, callback: Callable) -> None:
        if event_pattern not in self.listeners:
            self.listeners[event_pattern] = []
        if callback not in self.listeners[event_pattern]:
            self.listeners[event_pattern].append(callback)
            self.logger.info("Subscribed to pattern: %s", event_pattern)

    # ── Core emit / publish (unchanged) ───────────────────────────────────────

    async def emit(
        self,
        event_type : str,
        data       : Any = None,
        source     : str = None,
    ) -> None:
        """Push an event into the priority queue."""
        event = Event(event_type, data, source)

        event_lower = event_type.lower()
        if any(
            x in event_lower
            for x in ["stop", "abort", "security", "fail", "alert",
                       "routing_mismatch"]   # Phase 3: routing_mismatch is high-priority
        ):
            priority = self._PRIORITY_HIGH
        elif any(
            x in event_lower
            for x in ["log", "metric", "update", "state"]
        ):
            priority = self._PRIORITY_LOW
        else:
            priority = self._PRIORITY_DEFAULT

        await self._queue.put((priority, event))

    def publish(
        self,
        event_type : str,
        data       : Any = None,
        source     : str = None,
    ) -> None:
        """100% thread-safe event publishing."""
        if self._event_loop is None:
            self.logger.warning(
                "Event loop not initialized yet. Dropping event '%s'", event_type
            )
            return

        if not self._event_loop.is_running():
            self.logger.warning(
                "Event loop not running. Dropping event '%s'", event_type
            )
            return

        try:
            current_loop = asyncio.get_running_loop()
            if current_loop == self._event_loop:
                current_loop.create_task(self.emit(event_type, data, source))
                return
        except RuntimeError:
            pass

        try:
            self._event_loop.call_soon_threadsafe(
                self._schedule_event, event_type, data, source
            )
        except RuntimeError as exc:
            self.logger.error(
                "Failed to schedule event '%s': %s", event_type, exc
            )

    def _schedule_event(
        self, event_type: str, data: Any, source: str
    ) -> None:
        try:
            asyncio.create_task(self.emit(event_type, data, source))
        except Exception as exc:
            self.logger.error(
                "Failed to emit event '%s': %s", event_type, exc
            )

    # ── Phase 3 — typed routing_mismatch helpers ──────────────────────────────

    async def emit_routing_mismatch(self, data: dict) -> None:
        """
        Emit a structured "routing_mismatch" event at high priority.

        *data* is merged with _ROUTING_MISMATCH_DEFAULTS so every field
        is guaranteed to be present.  Subscribers (learner.py, logs.py)
        can rely on the schema without defensive key checks.

        Required fields (filled with defaults if absent):
            task_id, step_index, intent, method, failure_class,
            detail, fallback_chain, decision_log

        Added automatically:
            summary — human-readable string for dashboard display
        """
        payload = _build_routing_mismatch_payload(data)
        await self.emit("routing_mismatch", payload, source="event_bus")

    def publish_routing_mismatch(self, data: dict) -> None:
        """
        Synchronous wrapper for emit_routing_mismatch().

        Use this from non-async contexts (e.g. a thread that cannot await).
        Uses the standard thread-safe publish() path so no extra wiring
        is needed.
        """
        payload = _build_routing_mismatch_payload(data)
        self.publish("routing_mismatch", payload, source="event_bus")

    # ── Run loop (unchanged) ──────────────────────────────────────────────────

    async def run(self) -> None:
        self._event_loop = asyncio.get_running_loop()
        self.logger.info("Event Bus is running...")

        while True:
            priority, event = await self._queue.get()
            print(f"[Priority {priority}] {event}")

            matched_listeners: list[Callable] = []
            for pattern, callbacks in self.listeners.items():
                if fnmatch.fnmatch(event.name, pattern):
                    matched_listeners.extend(callbacks)

            for callback in matched_listeners:
                asyncio.create_task(self._execute_callback(callback, event))

            self._queue.task_done()

    async def _execute_callback(
        self, callback: Callable, event: Event
    ) -> None:
        # Guard: Qt objects may be deleted while their Python wrapper still
        # exists.  Calling a bound method on a deleted C++ object raises
        # RuntimeError("wrapped C/C++ object … has been deleted").
        bound_obj = getattr(callback, "__self__", None)
        if bound_obj is not None:
            is_valid_fn = getattr(bound_obj, "isValid", None) or getattr(
                bound_obj, "sip_isdeleted", None
            )
            if is_valid_fn is not None:
                try:
                    if callable(is_valid_fn) and not is_valid_fn():
                        self.logger.debug(
                            "Skipping dead Qt callback for %s — unsubscribing.",
                            event.name,
                        )
                        self._unsubscribe_dead(callback)
                        return
                except Exception:
                    pass

        try:
            if asyncio.iscoroutinefunction(callback):
                await callback(event)
            else:
                callback(event)
        except RuntimeError as exc:
            err_str = str(exc)
            if "wrapped C/C++ object" in err_str and "has been deleted" in err_str:
                self.logger.debug(
                    "Dead Qt object in listener for %s — removing callback.",
                    event.name,
                )
                self._unsubscribe_dead(callback)
            else:
                self.logger.error(
                    "Error in listener for %s: %s", event.name, exc
                )
        except Exception as exc:
            self.logger.error(
                "Error in listener for %s: %s", event.name, exc
            )

    def _unsubscribe_dead(self, callback: Callable) -> None:
        for pattern in list(self.listeners.keys()):
            try:
                self.listeners[pattern].remove(callback)
            except ValueError:
                pass


# Module-level singleton
bus = EventBus()