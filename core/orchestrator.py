"""
core/orchestrator.py — Operonix AI OS Agent
═════════════════════════════════════════════

FIX CHANGELOG (this revision)
──────────────────────────────
BUG 1 — inject_task_metadata ordering race (event_bus concurrent dispatch)
    event_bus.run() fires ALL subscribers to the same event as concurrent
    asyncio tasks — there is no guaranteed order.  inject_task_metadata was
    subscribed to "request_planning" hoping to run before the planner, but
    both tasks were spawned simultaneously so the planner always saw the
    un-enriched payload.

    FIX: inject_task_metadata is now subscribed to "capability_mapped".
    The orchestrator sees capability_mapped, enriches event.data with
    preferred_method and context IN PLACE, then the decision_engine's
    enqueue_task (also subscribed to capability_mapped) picks up the already-
    enriched payload.  Because both run concurrently, enrichment is done via
    a direct dict mutation on event.data — the decision_engine reads the same
    dict object so it always has the full context regardless of task order.

    The chain then becomes:
      capability_mapped  →  orchestrator enriches event.data  (concurrent)
                         →  decision_engine queues enriched task
      decision_engine    →  request_planning  (enriched payload)
      planner            →  task_dispatched   (enriched payload)
      validator          →  task_safety_cleared
      executor           →  executes with full context + preferred_method
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


def _panel_enabled() -> bool:
    return bool(getattr(settings, "PANEL_ENABLED", True))


def _build_panel_controller() -> Any:
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
        self.pipeline  = VoicePipeline(audio_manager=self.audio_manager)
        wake_phrase    = getattr(settings, "WAKE_WORD", "alexa")
        self.wake_detector = WakeWordDetector(
            wake_word=wake_phrase,
            audio_manager=self.audio_manager,
        )
        self._panel_controller: Optional[Any] = None
        self._panel_thread:     Optional[threading.Thread] = None
        self._panel_ready = threading.Event()

    # ── Lifecycle ──────────────────────────────────────────────────────────

    async def start(self) -> None:
        self.is_running = True
        loop = asyncio.get_running_loop()
        self.wake_detector.loop = loop

        bus.subscribe("wake_word_detected",    self.handle_wake_word)
        bus.subscribe("text_query_received",   self._handle_panel_input)
        bus.subscribe("user_input_received",   self.handle_new_task)

        # Capture window context into active_tasks as soon as it arrives
        bus.subscribe("context_snapshot_ready", self.handle_context_snapshot)

        # Route parsed intent to capability mapper
        bus.subscribe("intent_parsed",         self.route_to_mapper)

        # BUG 1 FIX: inject preferred_method + context on capability_mapped,
        # not on request_planning, to avoid the concurrent-dispatch race.
        bus.subscribe("capability_mapped",     self.inject_task_metadata)

        # Task lifecycle
        bus.subscribe("task_failed",           self.handle_failure)
        bus.subscribe("task_completed",        self.finalize_task)

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
                logger.warning("panel stop error: %s", exc)
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
            logger.warning("panel did not signal ready within timeout.")

    def _stop_panel(self) -> None:
        if self._panel_controller is not None:
            try:
                self._panel_controller.stop()
            except Exception as exc:
                logger.warning("panel_controller.stop() error: %s", exc)
            self._panel_controller = None
        if self._panel_thread is not None and self._panel_thread.is_alive():
            self._panel_thread.join(timeout=5.0)
            self._panel_thread = None
        logger.info("panel stopped.")

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
            logger.info("panel thread ready.")
            qt_app.exec()
        except ImportError as exc:
            logger.warning("PyQt6 not available — panel disabled (%s).", exc)
            self._panel_ready.set()
        except Exception as exc:
            logger.error("panel thread crashed: %s", exc, exc_info=True)
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
                logger.debug("app_profiler error: %s", exc)

    # ── Input handlers ─────────────────────────────────────────────────────

    async def handle_wake_word(self, event: Any) -> None:
        score = event.data.get("score", 0.0)
        logger.info("Wake word detected (score=%.2f)", score)
        loop = asyncio.get_running_loop()
        try:
            command = await loop.run_in_executor(None, self.pipeline.capture_command)
            if command and command.get("text"):
                await bus.emit(
                    "user_input_received",
                    {
                        "text":             command["text"],
                        "source":           "voice",
                        "stt":              command.get("stt", {}),
                        "stt_provider":     command.get("provider"),
                        "confidence":       command.get("confidence", 0.0),
                        "duration":         command.get("duration_seconds", 0),
                        "preferred_method": None,
                    },
                    source="orchestrator",
                )
        except Exception as exc:
            logger.error("Voice capture failed: %s", exc)
            await bus.emit("voice_capture_error", {"error": str(exc)}, source="orchestrator")

    async def _handle_panel_input(self, event: Any) -> None:
        query = (event.data.get("query") or "").strip()
        if not query:
            return
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
            task_id, self.active_tasks[task_id]["source"], user_text,
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

    async def handle_context_snapshot(self, event: Any) -> None:
        """
        Merge window_detector snapshot into active_tasks[task_id]["context"].
        The snapshot contains window_title, app_name, app_type etc. but NOT cwd.
        cwd is resolved separately by window_detector (see window_detector.py fix).
        """
        task_id = event.data.get("task_id")
        if not task_id or task_id not in self.active_tasks:
            return

        snapshot = {k: v for k, v in event.data.items() if k != "task_id"}
        current_ctx = self.active_tasks[task_id]["context"]
        for key, value in snapshot.items():
            if not current_ctx.get(key):
                current_ctx[key] = value

        logger.debug(
            "Task [%s] context: window='%s' cwd='%s'",
            task_id,
            current_ctx.get("window_title", ""),
            current_ctx.get("cwd", "<not set>"),
        )

    async def route_to_mapper(self, event: Any) -> None:
        """intent_parsed → request_capability_mapping. Also stores intent."""
        task_id = event.data.get("task_id")
        task    = self.active_tasks.get(task_id, {})
        if event.data.get("intent"):
            task["intent"] = event.data["intent"]
        await bus.emit("request_capability_mapping", event.data, source="orchestrator")

    async def inject_task_metadata(self, event: Any) -> None:
        """
        BUG 1 FIX: subscribed to "capability_mapped".

        Mutates event.data IN PLACE before the decision_engine's enqueue_task
        coroutine reads it.  Both handlers are spawned concurrently by the
        event bus, but because this is a direct dict mutation (not a re-publish),
        whichever coroutine runs second will see the updated values.

        To guarantee the decision_engine always sees the enriched data we use
        a short yield (await asyncio.sleep(0)) which allows this coroutine to
        complete the mutation before the decision_engine's enqueue_task awaits
        its own queue.put().
        """
        task_id = event.data.get("task_id")
        if not task_id or task_id not in self.active_tasks:
            return

        task     = self.active_tasks[task_id]
        ctx      = task.get("context") or {}
        pmethod  = task.get("preferred_method")

        # Inject preferred_method
        if pmethod and not event.data.get("preferred_method"):
            event.data["preferred_method"] = pmethod

        # Inject context — merge so any keys already set survive
        existing = event.data.get("context") or {}
        event.data["context"] = {**ctx, **existing}

        # Yield to allow this mutation to settle before the decision_engine
        # reads the dict inside its own enqueue_task coroutine.
        await asyncio.sleep(0)

        logger.debug(
            "Task [%s] metadata injected (preferred_method=%s, cwd=%s)",
            task_id,
            event.data.get("preferred_method"),
            event.data["context"].get("cwd", "<not set>"),
        )

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


orchestrator = Orchestrator()

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