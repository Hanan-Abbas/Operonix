"""
core/orchestrator.py — Operonix AI OS Agent
═════════════════════════════════════════════
Top-level coordinator.

FIX CHANGELOG (this revision)
──────────────────────────────
BUG 1 — route_to_executor() was subscribed to "capability_mapped".
    This caused the orchestrator to intercept capability_mapped and publish
    "request_execution" — an event nothing listens to — creating a dead-end
    parallel path that bypassed decision_engine → planner → validator entirely.
    The executor subscribes to "task_safety_cleared" on its own; the
    orchestrator must NOT short-circuit that chain.
    FIX: removed the "capability_mapped" subscription and route_to_executor().
    The correct chain is:
        capability_mapped
          → decision_engine  → request_planning
          → planner          → task_dispatched
          → safety_validator → task_safety_cleared
          → executor         (self-subscribed)

BUG 2 — context_snapshot_ready was never handled.
    window_detector publishes "context_snapshot_ready" with window_title,
    app_name, app_type, cwd etc.  The orchestrator never subscribed to it,
    so active_tasks[task_id]["context"] stayed {} forever.  The planner then
    dispatched context:{} and the executor could not resolve "current window"
    to a real path.
    FIX: added handle_context_snapshot() which merges the snapshot into
    active_tasks[task_id]["context"].

BUG 3 — preferred_method and context never reached the executor.
    Both were stored in active_tasks but only injected into "request_execution"
    (the dead-end event from BUG 1).  The planner dispatches "task_dispatched"
    using only the data it received from the decision_engine, which never had
    access to active_tasks.
    FIX: added "request_planning" subscription — inject_task_metadata() merges
    preferred_method and context from active_tasks into the planning payload
    before the planner sees it.  The planner then forwards both in
    "task_dispatched" (already fixed in planner.py).

BUG 4 (validator) — validator read step.get("intent") but steps use "action".
    FIX: applied in validator.py — reads step.get("action") with fallback to
    task_data.get("intent").
"""
from __future__ import annotations

import asyncio
import logging
import threading
import time
import uuid
from typing import Any, Optional

from core.config import settings
from core.event_bus import bus
from voice.audio_manager import AudioManager
from voice.wake_word import WakeWordDetector
from voice.pipeline import VoicePipeline

logger = logging.getLogger("Orchestrator")


# ---------------------------------------------------------------------------
# Helpers
# ---------------------------------------------------------------------------

def _panel_enabled() -> bool:
    return bool(getattr(settings, "PANEL_ENABLED", True))


def _build_panel_controller() -> Any:
    """
    Construct PanelController with all dependencies injected.
    Uses global singletons so the panel shares the populated registry.
    """
    from panel.panel_controller import PanelController
    from brain.intent_parser import IntentParser
    from capabilities.registry import capability_registry
    from plugins.loader import plugin_loader
    from learning.retriever import Retriever

    _intent_parser = IntentParser()

    try:
        _retriever = Retriever()
        def learned_ranking(app: str, intent: str) -> list[str]:
            try:
                return _retriever.get_method_ranking(app=app, intent=intent)
            except Exception:
                return []
    except Exception:
        learned_ranking = None  # type: ignore[assignment]

    return PanelController(
        event_bus=bus,
        intent_parser=lambda text: _intent_parser.parse(text),
        plugin_registry=lambda app, intent: plugin_loader.find(app=app, intent=intent),
        capability_registry=lambda intent: capability_registry.find(intent=intent),
        learned_ranking=learned_ranking,
    )


# ---------------------------------------------------------------------------
# Orchestrator
# ---------------------------------------------------------------------------

class Orchestrator:
    def __init__(self) -> None:
        self.active_tasks: dict[str, dict[str, Any]] = {}
        self.is_running: bool = False
        self._voice_active = True

        self.audio_manager = AudioManager(
            rate=int(getattr(settings, "AUDIO_RATE", 16000)),
            chunk=int(getattr(settings, "AUDIO_CHUNK", 1280)),
            auto_start=False,
        )
        self.pipeline = VoicePipeline(audio_manager=self.audio_manager)

        wake_phrase = getattr(settings, "WAKE_WORD", "alexa")
        self.wake_detector = WakeWordDetector(
            wake_word=wake_phrase,
            audio_manager=self.audio_manager,
        )

        self._panel_controller: Optional[Any] = None
        self._panel_thread: Optional[threading.Thread] = None
        self._panel_ready = threading.Event()

    # ── Lifecycle ─────────────────────────────────────────────────────────

    async def start(self) -> None:
        self.is_running = True
        loop = asyncio.get_running_loop()
        self.wake_detector.loop = loop

        # ── Event subscriptions ───────────────────────────────────────────
        # Voice / panel input normalisation
        bus.subscribe("wake_word_detected",    self.handle_wake_word)
        bus.subscribe("text_query_received",   self._handle_panel_input)
        bus.subscribe("user_input_received",   self.handle_new_task)

        # BUG 2 FIX: capture window context into active_tasks
        bus.subscribe("context_snapshot_ready", self.handle_context_snapshot)

        # Intent parsed → request capability mapping
        bus.subscribe("intent_parsed",         self.route_to_mapper)

        # BUG 3 FIX: inject preferred_method + context before planner runs
        bus.subscribe("request_planning",      self.inject_task_metadata)

        # Task lifecycle
        bus.subscribe("task_failed",           self.handle_failure)
        bus.subscribe("task_completed",        self.finalize_task)

        # NOTE: "capability_mapped" subscription REMOVED (BUG 1 FIX).
        # The chain capability_mapped → decision_engine → planner →
        # safety_validator → executor is fully self-contained via their
        # own bus subscriptions.  The orchestrator must not intercept it.

        asyncio.create_task(self._background_wake_loop())
        asyncio.create_task(self._emit_app_context_loop())

        logger.info(
            "Orchestrator started (wake-word=%r, panel=%s).",
            self.wake_detector.wake_word,
            "enabled" if _panel_enabled() else "disabled",
        )

    async def stop(self) -> None:
        self.is_running = False
        self.audio_manager.stop()
        if self._panel_controller is not None:
            try:
                loop = asyncio.get_running_loop()
                await loop.run_in_executor(None, self._stop_panel)
            except Exception as exc:
                logger.warning("Orchestrator: panel stop error — %s", exc)
        logger.info("Orchestrator stopped.")

    # ── Panel bootstrap ────────────────────────────────────────────────────

    def _start_panel(self) -> None:
        if self._panel_thread is not None and self._panel_thread.is_alive():
            return
        self._panel_ready.clear()
        self._panel_thread = threading.Thread(
            target=self._panel_thread_target,
            name="operonix-panel",
            daemon=True,
        )
        self._panel_thread.start()
        ready = self._panel_ready.wait(
            timeout=float(getattr(settings, "PANEL_START_TIMEOUT", 10.0))
        )
        if not ready:
            logger.warning("Orchestrator: panel did not signal ready within timeout.")

    def _stop_panel(self) -> None:
        if self._panel_controller is not None:
            try:
                self._panel_controller.stop()
            except Exception as exc:
                logger.warning("Orchestrator: panel_controller.stop() error — %s", exc)
            self._panel_controller = None
        if self._panel_thread is not None and self._panel_thread.is_alive():
            self._panel_thread.join(timeout=5.0)
            self._panel_thread = None
        logger.info("Orchestrator: panel stopped.")

    def _panel_thread_target(self) -> None:
        try:
            from PyQt6.QtWidgets import QApplication
            import sys
            from pathlib import Path

            state_dir = Path.home() / ".operonix"
            state_dir.mkdir(parents=True, exist_ok=True)

            qt_app = QApplication.instance() or QApplication(sys.argv)
            self._panel_controller = _build_panel_controller()
            self._panel_controller.start(loop=None)
            self._panel_ready.set()
            logger.info("Orchestrator: panel thread ready.")
            qt_app.exec()
        except ImportError as exc:
            logger.warning("Orchestrator: PyQt6 not available — panel disabled (%s).", exc)
            self._panel_ready.set()
        except Exception as exc:
            logger.error("Orchestrator: panel thread crashed — %s", exc, exc_info=True)
            self._panel_ready.set()

    # ── Background loops ───────────────────────────────────────────────────

    async def _background_wake_loop(self) -> None:
        loop = asyncio.get_running_loop()
        while self.is_running:
            if getattr(self, "_voice_active", True):
                await loop.run_in_executor(None, self.wake_detector.detect)
            await asyncio.sleep(0.005)

    async def _emit_app_context_loop(self) -> None:
        interval = float(getattr(settings, "APP_CONTEXT_POLL_INTERVAL", 2.0))
        last_app: str = ""
        while self.is_running:
            await asyncio.sleep(interval)
            try:
                from context.app_profiler import AppProfiler
                current_app = AppProfiler.get_active_app_name()
                if current_app and current_app != last_app:
                    last_app = current_app
                    await bus.emit(
                        "app_context_changed",
                        {"app_name": current_app},
                        source="orchestrator",
                    )
            except Exception as exc:
                logger.debug("Orchestrator: app_profiler error — %s", exc)

    # ── Wake-word handler ──────────────────────────────────────────────────

    async def handle_wake_word(self, event: Any) -> None:
        score = event.data.get("score", 0.0)
        logger.info("Wake word detected (score=%.2f) — capturing command", score)
        loop = asyncio.get_running_loop()
        try:
            command = await loop.run_in_executor(None, self.pipeline.capture_command)
            if command and command.get("text"):
                text = command["text"]
                confidence = command.get("confidence", 0.0)
                logger.info("Command (conf=%.2f): '%s'", confidence, text)
                await bus.emit(
                    "user_input_received",
                    {
                        "text":             text,
                        "source":           "voice",
                        "stt":              command.get("stt", {}),
                        "stt_provider":     command.get("provider"),
                        "confidence":       confidence,
                        "duration":         command.get("duration_seconds", 0),
                        "preferred_method": None,
                    },
                    source="orchestrator",
                )
            else:
                logger.info("Command capture returned None (likely silence)")
        except Exception as exc:
            logger.error("Voice capture failed: %s", exc)
            await bus.emit("voice_capture_error", {"error": str(exc)}, source="orchestrator")

    # ── Panel input normaliser ─────────────────────────────────────────────

    async def _handle_panel_input(self, event: Any) -> None:
        query = (event.data.get("query") or "").strip()
        if not query:
            return
        logger.info(
            "Panel input: '%s' (preferred_method=%s)",
            query,
            event.data.get("preferred_method", "auto"),
        )
        await bus.emit(
            "user_input_received",
            {
                "text":             query,
                "source":           "panel",
                "stt":              {},
                "stt_provider":     None,
                "confidence":       1.0,
                "duration":         0,
                "preferred_method": event.data.get("preferred_method"),
            },
            source="orchestrator",
        )

    # ── Task lifecycle ─────────────────────────────────────────────────────

    async def handle_new_task(self, event: Any) -> None:
        task_id   = str(uuid.uuid4())[:8]
        user_text = (event.data.get("text") or "").strip()
        if not user_text:
            return

        self.active_tasks[task_id] = {
            "status":           "gathering_context",
            "input":            user_text,
            "source":           event.data.get("source", "unknown"),
            "preferred_method": event.data.get("preferred_method"),
            "context":          {},
            "started_at":       time.monotonic(),
        }
        logger.info(
            "Task [%s] initialised (source=%s): %r",
            task_id,
            self.active_tasks[task_id]["source"],
            user_text,
        )

        await bus.emit("request_context_snapshot", {"task_id": task_id}, source="orchestrator")
        await bus.emit(
            "request_intent_parsing",
            {
                "task_id":          task_id,
                "text":             user_text,
                "stt":              event.data.get("stt") or {},
                "stt_provider":     event.data.get("stt_provider"),
                "preferred_method": event.data.get("preferred_method"),
            },
            source="orchestrator",
        )

    # ── BUG 2 FIX — capture window context snapshot ────────────────────────

    async def handle_context_snapshot(self, event: Any) -> None:
        """
        window_detector publishes context_snapshot_ready with:
            window_title, app_name, app_type, cwd, app_context, task_id

        Merge this into active_tasks[task_id]["context"] so the planner
        and executor have a real cwd and window_title to work with.
        """
        task_id = event.data.get("task_id")
        if not task_id or task_id not in self.active_tasks:
            return

        snapshot = dict(event.data)
        snapshot.pop("task_id", None)   # don't nest task_id inside context

        # Merge: existing keys are NOT overwritten so manual overrides survive
        current_ctx = self.active_tasks[task_id]["context"]
        for key, value in snapshot.items():
            if key not in current_ctx or not current_ctx[key]:
                current_ctx[key] = value

        logger.debug(
            "Task [%s] context updated: window='%s' cwd='%s'",
            task_id,
            current_ctx.get("window_title", ""),
            current_ctx.get("cwd", ""),
        )

    # ── BUG 3 FIX — inject metadata before planner runs ───────────────────

    async def inject_task_metadata(self, event: Any) -> None:
        """
        Subscribed to "request_planning".

        The decision_engine publishes request_planning with the capability
        payload, but it has no access to active_tasks so preferred_method
        and the full window context are missing.

        This handler enriches the payload in-place on the event so the
        planner (which subscribes to the same event) receives complete data.

        Subscription order: orchestrator registers first (in start()), so
        this runs before the planner's handler.
        """
        task_id = event.data.get("task_id")
        if not task_id or task_id not in self.active_tasks:
            return

        task = self.active_tasks[task_id]

        # Inject preferred_method if not already set by decision_engine
        if not event.data.get("preferred_method") and task.get("preferred_method"):
            event.data["preferred_method"] = task["preferred_method"]

        # Inject full context — merge so any keys decision_engine set survive
        existing_ctx = event.data.get("context") or {}
        task_ctx     = task.get("context") or {}
        merged_ctx   = {**task_ctx, **existing_ctx}   # existing wins on conflict
        event.data["context"] = merged_ctx

        logger.debug(
            "Task [%s] metadata injected into request_planning "
            "(preferred_method=%s, cwd=%s)",
            task_id,
            event.data.get("preferred_method"),
            merged_ctx.get("cwd", ""),
        )

    # ── Intent routing ─────────────────────────────────────────────────────

    async def route_to_mapper(self, event: Any) -> None:
        """intent_parsed → request_capability_mapping"""
        task_id = event.data.get("task_id")
        task    = self.active_tasks.get(task_id, {})

        # Store intent for use in finalize_task / handle_failure
        if event.data.get("intent"):
            task["intent"] = event.data["intent"]

        await bus.emit("request_capability_mapping", event.data, source="orchestrator")

    # ── Task completion ────────────────────────────────────────────────────

    async def handle_failure(self, event: Any) -> None:
        task_id    = event.data.get("task_id")
        error      = event.data.get("error")
        task       = self.active_tasks.get(task_id, {})
        logger.error("Task [%s] failed: %s", task_id, error)

        elapsed_ms = int(
            (time.monotonic() - task.get("started_at", time.monotonic())) * 1000
        )
        await bus.emit(
            "action_completed",
            {
                "task_id":     task_id,
                "query":       task.get("input", ""),
                "intent":      task.get("intent"),
                "method":      task.get("method_used", "unknown"),
                "success":     False,
                "duration_ms": elapsed_ms,
                "error":       error,
            },
            source="orchestrator",
        )
        await bus.emit(
            "error_detected",
            {"task_id": task_id, "error": error, "context": task.get("context")},
            source="orchestrator",
        )

    async def finalize_task(self, event: Any) -> None:
        task_id    = event.data.get("task_id")
        task       = self.active_tasks.pop(task_id, {})
        elapsed_ms = int(
            (time.monotonic() - task.get("started_at", time.monotonic())) * 1000
        )
        logger.info("Task [%s] completed in %d ms.", task_id, elapsed_ms)

        await bus.emit(
            "action_completed",
            {
                "task_id":     task_id,
                "query":       task.get("input", ""),
                "intent":      event.data.get("intent") or task.get("intent"),
                "method":      event.data.get("method_used") or task.get("method_used", "unknown"),
                "success":     True,
                "duration_ms": elapsed_ms,
            },
            source="orchestrator",
        )


# ── Singleton ──────────────────────────────────────────────────────────────
orchestrator = Orchestrator()


# ── Standalone entry point ─────────────────────────────────────────────────
if __name__ == "__main__":
    logging.basicConfig(level=logging.INFO)

    async def _main() -> None:
        await orchestrator.start()
        try:
            while True:
                await asyncio.sleep(1)
        except asyncio.CancelledError:
            pass
        finally:
            await orchestrator.stop()

    try:
        asyncio.run(_main())
    except KeyboardInterrupt:
        print("\nOrchestrator stopped.")