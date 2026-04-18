"""
core/orchestrator.py — Operonix AI OS Agent
═════════════════════════════════════════════
Top-level coordinator.

Input sources (both equal, no special casing):
  • Voice pipeline  → fires  transcription_complete  → normalised to user_input_received
  • Panel (text UI) → fires  text_query_received     → normalised to user_input_received

Panel integration:
  • PanelController is created here and started in a dedicated Qt thread.
  • The Orchestrator exposes four thin adapter methods that translate
    execution outcomes back into EventBus events the panel understands:
      action_completed, app_context_changed
  • preferred_method from the panel payload is forwarded to the executor
    so user strategy overrides are honoured end-to-end.

Changes vs previous version:
  • Panel boot sequence added (_start_panel, _panel_thread_target)
  • text_query_received subscription + _handle_panel_input normaliser
  • handle_new_task now accepts an optional preferred_method field
  • finalize_task / handle_failure now emit action_completed for the panel
  • _emit_app_context_loop publishes app_context_changed periodically
  • No double-routing bug (unchanged from prior fix)
  • Graceful shutdown calls panel_controller.stop()
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
# Helpers — lazy imports so Qt is only loaded when the panel is enabled
# ---------------------------------------------------------------------------

def _panel_enabled() -> bool:
    """Read PANEL_ENABLED from settings; default True."""
    return bool(getattr(settings, "PANEL_ENABLED", True))


def _build_panel_controller() -> Any:
    """
    Construct PanelController with all dependencies injected.
    All subsystem callables are wrapped so the panel never imports
    brain/ or capabilities/ directly.
    """
    from panel.panel_controller import PanelController
    from brain.intent_parser import IntentParser
    from capabilities.registry import CapabilityRegistry
    from plugins.loader import PluginLoader
    from learning.retriever import Retriever

    _intent_parser = IntentParser()
    _cap_registry = CapabilityRegistry()
    _plugin_loader = PluginLoader()

    # Wrap retriever for learned ranking; returns [] gracefully on failure.
    try:
        _retriever = Retriever()
        def learned_ranking(app: str, intent: str) -> list[str]:
            try:
                return _retriever.get_method_ranking(app=app, intent=intent)
            except Exception:  # noqa: BLE001
                return []
    except Exception:  # noqa: BLE001
        learned_ranking = None  # type: ignore[assignment]

    return PanelController(
        event_bus=bus,
        intent_parser=lambda text: _intent_parser.parse(text),
        plugin_registry=lambda app, intent: _plugin_loader.find(app=app, intent=intent),
        capability_registry=lambda intent: _cap_registry.find(intent=intent),
        learned_ranking=learned_ranking,
    )


# ---------------------------------------------------------------------------
# Orchestrator
# ---------------------------------------------------------------------------

class Orchestrator:
    def __init__(self) -> None:
        self.active_tasks: dict[str, dict[str, Any]] = {}
        self.is_running: bool = False

        # ── Single AudioManager ───────────────────────────────────────────────
        self.audio_manager = AudioManager(
            rate=int(getattr(settings, "AUDIO_RATE", 16000)),
            chunk=int(getattr(settings, "AUDIO_CHUNK", 1280)),
            auto_start=True,
        )

        self.pipeline = VoicePipeline(audio_manager=self.audio_manager)

        wake_phrase = getattr(settings, "WAKE_WORD", "alexa")
        self.wake_detector = WakeWordDetector(
            wake_word=wake_phrase,
            audio_manager=self.audio_manager,
        )

        # Panel — created lazily in _start_panel()
        self._panel_controller: Optional[Any] = None
        self._panel_thread: Optional[threading.Thread] = None
        self._panel_ready = threading.Event()

    # ── Lifecycle ─────────────────────────────────────────────────────────────

    async def start(self) -> None:
        self.is_running = True

        loop = asyncio.get_running_loop()
        self.wake_detector.loop = loop

        # Voice subscriptions (unchanged)
        bus.subscribe("wake_word_detected",   self.handle_wake_word)
        bus.subscribe("user_input_received",  self.handle_new_task)

        # Panel subscriptions
        bus.subscribe("text_query_received",  self._handle_panel_input)
        bus.subscribe("intent_parsed",        self.route_to_mapper)
        bus.subscribe("capability_mapped",    self.route_to_executor)
        bus.subscribe("task_failed",          self.handle_failure)
        bus.subscribe("task_completed",       self.finalize_task)

        asyncio.create_task(self._background_wake_loop())
        asyncio.create_task(self._emit_app_context_loop())

        logger.info(
            "👂 Orchestrator started (wake-word=%r, panel=%s).",
            self.wake_detector.wake_word,
            "enabled" if _panel_enabled() else "disabled",
        )

        # Start the panel in its own OS thread (Qt requires its own thread
        # if the main thread is the asyncio event loop).
        if _panel_enabled():
            self._start_panel()

    async def stop(self) -> None:
        self.is_running = False
        self.audio_manager.stop()

        if self._panel_controller is not None:
            try:
                self._panel_controller.stop()
            except Exception as exc:  # noqa: BLE001
                logger.warning("Orchestrator: panel stop error — %s", exc)

        logger.info("🛑 Orchestrator stopped.")

    # ── Panel bootstrap ───────────────────────────────────────────────────────

    def _start_panel(self) -> None:
        """
        Spin up the Qt event loop in a daemon thread.
        Qt widgets must live in a single thread; we isolate that thread here
        so it never blocks the asyncio event loop.
        """
        self._panel_thread = threading.Thread(
            target=self._panel_thread_target,
            name="operonix-panel",
            daemon=True,
        )
        self._panel_thread.start()
        # Give the panel up to 5 s to initialise before the rest of start() continues.
        ready = self._panel_ready.wait(timeout=float(getattr(settings, "PANEL_START_TIMEOUT", 5.0)))
        if not ready:
            logger.warning("Orchestrator: panel did not signal ready within timeout.")

    def _panel_thread_target(self) -> None:
        """
        Runs in the panel daemon thread.
        Creates the QApplication, builds the PanelController, and enters
        the Qt event loop.  Signals _panel_ready once the window is shown.
        """
        try:
            from PyQt6.QtWidgets import QApplication
            import sys

            qt_app = QApplication.instance() or QApplication(sys.argv)

            self._panel_controller = _build_panel_controller()

            # Pass the asyncio loop so the controller can schedule coroutines
            # back onto it from the Qt thread.
            main_loop = asyncio.get_event_loop()
            self._panel_controller.start(loop=main_loop)

            self._panel_ready.set()
            logger.info("Orchestrator: panel thread ready.")
            qt_app.exec()

        except ImportError as exc:
            logger.warning(
                "Orchestrator: PyQt6 not available — panel disabled (%s).", exc
            )
            self._panel_ready.set()
        except Exception as exc:  # noqa: BLE001
            logger.error("Orchestrator: panel thread crashed — %s", exc, exc_info=True)
            self._panel_ready.set()

    # ── Background loops ──────────────────────────────────────────────────────

    async def _background_wake_loop(self) -> None:
        loop = asyncio.get_running_loop()
        while self.is_running:
            await loop.run_in_executor(None, self.wake_detector.detect)
            await asyncio.sleep(0.005)

    async def _emit_app_context_loop(self) -> None:
        """
        Periodically publish app_context_changed so the panel badge and
        suggestion engine stay in sync with the user's active window.

        The context/app_profiler.py is expected to subscribe to this event
        (or this loop can be replaced by a focus_tracker callback — see
        context/focus_tracker.py).
        """
        interval = float(getattr(settings, "APP_CONTEXT_POLL_INTERVAL", 2.0))
        last_app: str = ""

        while self.is_running:
            await asyncio.sleep(interval)
            try:
                from context.app_profiler import AppProfiler  # lazy import; avoids circular dep
                current_app = AppProfiler.get_active_app_name()
                if current_app and current_app != last_app:
                    last_app = current_app
                    await bus.emit(
                        "app_context_changed",
                        {"app_name": current_app},
                        source="orchestrator",
                    )
                    logger.debug("Orchestrator: app context → %s", current_app)
            except Exception as exc:  # noqa: BLE001
                logger.debug("Orchestrator: app_profiler error — %s", exc)

    # ── Wake-word handler (voice path) ────────────────────────────────────────

    async def handle_wake_word(self, event: Any) -> None:
        score = event.data.get("score", 0.0)
        logger.info("🔔 Wake word detected (score=%.2f) — capturing command", score)

        loop = asyncio.get_running_loop()
        try:
            command = await loop.run_in_executor(None, self.pipeline.capture_command)

            if command and command.get("text"):
                text = command["text"]
                confidence = command.get("confidence", 0.0)
                logger.info("🎤 Command (conf=%.2f): '%s'", confidence, text)

                await bus.emit(
                    "user_input_received",
                    {
                        "text": text,
                        "source": "voice",
                        "stt": command.get("stt", {}),
                        "stt_provider": command.get("provider"),
                        "confidence": confidence,
                        "duration": command.get("duration_seconds", 0),
                        "preferred_method": None,   # voice never overrides method
                    },
                    source="orchestrator",
                )
            else:
                logger.info("🔇 Command capture returned None (likely silence)")

        except Exception as exc:
            logger.error("❌ Voice capture failed: %s", exc)
            await bus.emit(
                "voice_capture_error",
                {"error": str(exc)},
                source="orchestrator",
            )

    # ── Panel input normaliser ────────────────────────────────────────────────

    async def _handle_panel_input(self, event: Any) -> None:
        """
        text_query_received  →  user_input_received

        The panel fires text_query_received with an optional preferred_method
        field that carries the user's strategy override (plugin/api/command/ui).
        We normalise it into the same envelope the voice path uses so
        handle_new_task and all downstream handlers are completely unaware of
        the input source.
        """
        query = (event.data.get("query") or "").strip()
        if not query:
            return

        logger.info(
            "🖥️  Panel input: '%s' (preferred_method=%s)",
            query,
            event.data.get("preferred_method", "auto"),
        )

        await bus.emit(
            "user_input_received",
            {
                "text": query,
                "source": "panel",
                "stt": {},
                "stt_provider": None,
                "confidence": 1.0,          # text input is not transcribed; assume perfect
                "duration": 0,
                "preferred_method": event.data.get("preferred_method"),
            },
            source="orchestrator",
        )

    # ── Task lifecycle ────────────────────────────────────────────────────────

    async def handle_new_task(self, event: Any) -> None:
        """
        Unified entry point for both voice and panel inputs.
        preferred_method (may be None) is threaded through the task dict so
        the executor can honour panel strategy overrides.
        """
        task_id = str(uuid.uuid4())[:8]
        user_text = (event.data.get("text") or "").strip()
        if not user_text:
            return

        self.active_tasks[task_id] = {
            "status": "gathering_context",
            "input": user_text,
            "source": event.data.get("source", "unknown"),
            "preferred_method": event.data.get("preferred_method"),
            "context": {},
            "started_at": time.monotonic(),
        }
        logger.info(
            "🎛️  Task [%s] initialised (source=%s): %r",
            task_id,
            self.active_tasks[task_id]["source"],
            user_text,
        )

        await bus.emit(
            "request_context_snapshot",
            {"task_id": task_id},
            source="orchestrator",
        )
        await bus.emit(
            "request_intent_parsing",
            {
                "task_id": task_id,
                "text": user_text,
                "stt": event.data.get("stt") or {},
                "stt_provider": event.data.get("stt_provider"),
                "preferred_method": event.data.get("preferred_method"),
            },
            source="orchestrator",
        )

    async def route_to_mapper(self, event: Any) -> None:
        await bus.emit("request_capability_mapping", event.data, source="orchestrator")

    async def route_to_executor(self, event: Any) -> None:
        """
        Forward preferred_method into the execution request so the executor
        can skip the waterfall and jump straight to the user-chosen method.
        """
        task_id = event.data.get("task_id")
        task = self.active_tasks.get(task_id, {})

        payload = dict(event.data)
        # Only inject preferred_method if the task carried one (panel override).
        if task.get("preferred_method") and "preferred_method" not in payload:
            payload["preferred_method"] = task["preferred_method"]

        await bus.emit("request_execution", payload, source="orchestrator")

    async def handle_failure(self, event: Any) -> None:
        task_id = event.data.get("task_id")
        error = event.data.get("error")
        task = self.active_tasks.get(task_id, {})
        logger.error("❌ Task [%s] failed: %s", task_id, error)

        elapsed_ms = int((time.monotonic() - task.get("started_at", time.monotonic())) * 1000)

        # Notify the panel so it can update the status bar and history row.
        await bus.emit(
            "action_completed",
            {
                "task_id": task_id,
                "query": task.get("input", ""),
                "intent": task.get("intent"),
                "method": task.get("method_used", "unknown"),
                "success": False,
                "duration_ms": elapsed_ms,
                "error": error,
            },
            source="orchestrator",
        )

        await bus.emit(
            "error_detected",
            {
                "task_id": task_id,
                "error": error,
                "context": task.get("context"),
            },
            source="orchestrator",
        )

    async def finalize_task(self, event: Any) -> None:
        task_id = event.data.get("task_id")
        task = self.active_tasks.pop(task_id, {})
        elapsed_ms = int((time.monotonic() - task.get("started_at", time.monotonic())) * 1000)

        logger.info("✅ Task [%s] completed in %d ms.", task_id, elapsed_ms)

        # Notify the panel so it can show ✓ in the history list and update
        # the history_store row with the real outcome data.
        await bus.emit(
            "action_completed",
            {
                "task_id": task_id,
                "query": task.get("input", ""),
                "intent": event.data.get("intent") or task.get("intent"),
                "method": event.data.get("method_used") or task.get("method_used", "unknown"),
                "success": True,
                "duration_ms": elapsed_ms,
            },
            source="orchestrator",
        )


# ── Singleton ─────────────────────────────────────────────────────────────────
orchestrator = Orchestrator()


# ── Standalone entry point ────────────────────────────────────────────────────
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
        print("\n🛑 Orchestrator stopped.")