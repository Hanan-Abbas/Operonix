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
        self._voice_active = True

        # ── Single AudioManager ───────────────────────────────────────────────
        # auto_start=False — the mic must NOT open at import time.
        # Orchestrator() is instantiated at module level (bottom of this file),
        # which runs before lifecycle_manager.startup() and before
        # mode_manager.initialise() are called.
        # ModeManager._startup_voice() calls audio_manager.start() when the
        # system enters VOICE mode. ModeManager._teardown_voice() calls
        # audio_manager.stop() when leaving VOICE mode. If CURRENT_MODE=panel
        # at boot the mic is never opened at all.
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

        # NOTE: _start_panel() is intentionally NOT called here.
        # mode_manager._apply_mode() will call _startup_panel() →
        # _start_panel() once immediately after orchestrator.start()
        # returns in lifecycle_manager.startup(). Calling it here too
        # was the source of the double-panel bug.

    async def stop(self) -> None:
        self.is_running = False
        self.audio_manager.stop()

        if self._panel_controller is not None:
            try:
                loop = asyncio.get_running_loop()
                await loop.run_in_executor(None, self._stop_panel)
            except Exception as exc:  # noqa: BLE001
                logger.warning("Orchestrator: panel stop error — %s", exc)

        logger.info("🛑 Orchestrator stopped.")

    # ── Panel bootstrap ───────────────────────────────────────────────────────

    def _start_panel(self) -> None:
        """
        Spin up the Qt event loop in a daemon thread.

        GUARD: if the panel thread is already alive this method returns
        immediately. This prevents a second PanelWindow from being created
        when mode_manager calls _start_panel() while the thread from the
        initial boot is still running.
        """
        # ── Re-entry guard ───────────────────────────────────────────────────
        if self._panel_thread is not None and self._panel_thread.is_alive():
            logger.info(
                "Orchestrator: _start_panel() called but panel thread is already alive — skipping."
            )
            return

        # Reset the ready event for this (re-)start.
        self._panel_ready.clear()

        self._panel_thread = threading.Thread(
            target=self._panel_thread_target,
            name="operonix-panel",
            daemon=True,
        )
        self._panel_thread.start()

        # Give the panel up to PANEL_START_TIMEOUT seconds to initialise.
        ready = self._panel_ready.wait(
            timeout=float(getattr(settings, "PANEL_START_TIMEOUT", 10.0))
        )
        if not ready:
            logger.warning("Orchestrator: panel did not signal ready within timeout.")

    def _stop_panel(self) -> None:
        """
        Stop the panel controller and wait for the Qt thread to exit.

        Called from:
          • orchestrator.stop() (via run_in_executor)
          • mode_manager._teardown_panel() (via run_in_executor)

        Delegates asyncio-loop teardown to panel_controller.stop() which
        calls loop.call_soon_threadsafe(loop.stop) on the loop it owns.
        We then join the Qt thread with a short timeout.
        """
        if self._panel_controller is not None:
            try:
                self._panel_controller.stop()
            except Exception as exc:  # noqa: BLE001
                logger.warning("Orchestrator: panel_controller.stop() error — %s", exc)
            self._panel_controller = None

        if self._panel_thread is not None and self._panel_thread.is_alive():
            self._panel_thread.join(timeout=5.0)
            if self._panel_thread.is_alive():
                logger.warning("Orchestrator: panel thread did not exit within 5 s.")
            self._panel_thread = None

        logger.info("Orchestrator: panel stopped.")

    def _panel_thread_target(self) -> None:
        """
        Runs in the panel daemon thread.

        Creates QApplication and runs qt_app.exec() on this thread.
        The asyncio loop is NOT created or owned here — panel_controller
        creates its own "panel-asyncio" daemon thread for that.

        FIX: The previous version created an asyncio loop here, passed it
        to panel_controller.start(), and then called loop.close() in the
        finally block. panel_controller.start() was already spinning that
        loop via run_forever() in a separate thread, so loop.close() raced
        with run_forever() and raised RuntimeError: Cannot close a running
        event loop.

        The fix is simply to let panel_controller manage its own loop
        entirely (pass loop=None) and remove the loop.close() call.
        """
        try:
            from PyQt6.QtWidgets import QApplication
            import sys

            # Ensure the ~/.operonix directory exists before any Qt or
            # panel code tries to write panel_state.json.  This is the
            # earliest safe point — before PanelController.__init__ or
            # PanelConfig touch the filesystem.
            from pathlib import Path
            state_dir = Path.home() / ".operonix"
            state_dir.mkdir(parents=True, exist_ok=True)

            # Create QApplication on this thread (Qt requires that the
            # QApplication and all widgets live on the same thread).
            qt_app = QApplication.instance() or QApplication(sys.argv)

            # Build the controller and let it manage its own asyncio loop.
            # Passing loop=None causes panel_controller.start() to create
            # a new event loop and run it in its own "panel-asyncio" thread.
            self._panel_controller = _build_panel_controller()
            self._panel_controller.start(loop=None)

            self._panel_ready.set()
            logger.info("Orchestrator: panel thread ready.")

            # Block this thread on Qt's event loop.
            # When the user closes the panel or stop() is called,
            # panel_controller.stop() calls qt_app.quit() (via the window)
            # which causes exec() to return.
            qt_app.exec()

        except ImportError as exc:
            logger.warning(
                "Orchestrator: PyQt6 not available — panel disabled (%s).", exc
            )
            self._panel_ready.set()
        except Exception as exc:  # noqa: BLE001
            logger.error("Orchestrator: panel thread crashed — %s", exc, exc_info=True)
            self._panel_ready.set()
        # NOTE: No finally: loop.close() here. The asyncio loop is owned
        # by panel_controller's "panel-asyncio" thread. panel_controller.stop()
        # is responsible for stopping it cleanly via loop.call_soon_threadsafe.

    # ── Background loops ──────────────────────────────────────────────────────

    async def _background_wake_loop(self) -> None:
        loop = asyncio.get_running_loop()
        while self.is_running:
            if getattr(self, '_voice_active', True):
                await loop.run_in_executor(None, self.wake_detector.detect)
            await asyncio.sleep(0.005)

    async def _emit_app_context_loop(self) -> None:
        """
        Periodically publish app_context_changed so the panel badge and
        suggestion engine stay in sync with the user's active window.

        FIX: renderer.set_app_context() is a Qt widget call. Calling it
        directly from this asyncio coroutine (which runs on the asyncio
        thread, not the Qt thread) was the root cause of the segfault on
        mode switch. The EventBus handler in panel_controller now uses
        QMetaObject.invokeMethod to post the call onto the Qt thread.
        This loop itself only publishes the event — no Qt calls here.
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