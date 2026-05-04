"""
panel/panel_controller.py

The critical wiring file.
Connects the panel UI ↔ EventBus.

FIX CHANGELOG (Step 2):
  • _QtBridge(QObject) inner class replaces the fragile _invoke_on_qt_thread
    helper. The bridge is created on the Qt thread during start() and owns
    typed pyqtSignals for every cross-thread UI update. Emitting a signal
    from any thread is safe in Qt — the connected slot always executes on
    the receiving object's thread (QueuedConnection when crossing threads).
    This fully fixes the "app badge never updates" bug.

  • Mode switcher wired via EventBus (high-priority):
      panel renderer emits mode_change_requested(str)
      → controller publishes panel_mode_switch_requested on bus
      → mode_manager subscribes at priority=10 and calls set_mode()
    The bridge carries sig_set_active_mode to highlight the active button
    on any mode change (including changes from the dashboard).

  • stop() uses bridge.sig_request_quit to post QApplication.quit()
    onto the Qt thread safely.

FIX CHANGELOG (Bug #1 — wrong cwd when panel is active):
  • HotkeyListener now receives window_detector and panel_state so it can
    snapshot the active window context the instant the hotkey fires, before
    the panel window appears on screen. The snapshot is stored in
    panel_state.pre_panel_context (a runtime-only, non-persisted attribute).

  • _on_query_submitted() reads panel_state.pre_panel_context and injects
    its cwd into the "text_query_received" payload so the orchestrator always
    receives the user's real working directory, not the Operonix panel's cwd.
"""
from __future__ import annotations

import asyncio
import logging
import time
from typing import Any

from panel.history_store import HistoryStore
from panel.hotkey_listener import HotkeyListener
from panel.panel_config import PanelConfig
from panel.panel_renderer import PanelRenderer
from panel.panel_window import PanelWindow
from panel.snippet_store import SnippetStore
from panel.suggestion_engine import SuggestionEngine

log = logging.getLogger(__name__)

_HAS_QT = False
try:
    from PyQt6.QtCore import QObject, pyqtSignal as _pyqtSignal
    _HAS_QT = True
except ImportError:
    pass


# ---------------------------------------------------------------------------
# Qt bridge — created on the Qt thread, emitted from any thread
# ---------------------------------------------------------------------------

if _HAS_QT:
    class _QtBridge(QObject):
        """
        Thread-safe signal bridge between asyncio callbacks and the Qt GUI.

        Created inside PanelController.start() which runs on the Qt thread,
        so this object's thread affinity is correctly set to the Qt thread.
        Emitting any signal from a background thread causes Qt to queue the
        delivery and execute the connected slot on the Qt thread automatically.
        """
        sig_set_app_context     = _pyqtSignal(str)
        sig_set_status          = _pyqtSignal(str, str)       # message, level
        sig_push_history        = _pyqtSignal(str, str, bool) # text, method, success
        sig_set_resolved_intent = _pyqtSignal(str)
        sig_update_suggestions  = _pyqtSignal(object)         # SuggestionResult
        sig_set_active_mode     = _pyqtSignal(str)            # mode value string
        sig_toggle              = _pyqtSignal()
        sig_apply_theme         = _pyqtSignal()
        sig_request_quit        = _pyqtSignal()

        def __init__(self, parent: QObject | None = None) -> None:
            super().__init__(parent)

else:
    class _QtBridge:  # type: ignore[no-redef]
        """Stub when PyQt6 is unavailable."""
        def __init__(self, *a: Any, **kw: Any) -> None: pass
        def __getattr__(self, name: str) -> Any:
            class _Noop:
                def connect(self, *a: Any) -> None: pass
                def emit(self, *a: Any) -> None: pass
            return _Noop()


# ---------------------------------------------------------------------------
# Controller
# ---------------------------------------------------------------------------

class PanelController:
    """Owns all panel subsystems and wires them together."""

    def __init__(
        self,
        event_bus: Any,
        intent_parser: Any,
        plugin_registry: Any,
        capability_registry: Any,
        learned_ranking: Any = None,
        qt_app: Any = None,
    ) -> None:
        self._bus = event_bus
        self._qt_app = qt_app
        self._pending_row_id: int | None = None
        self._pending_start_ms: float = 0.0

        self._config = PanelConfig(event_bus)
        self._state = self._config.state

        self._history = HistoryStore()
        self._snippets = SnippetStore()

        self._engine = SuggestionEngine(
            intent_parser=intent_parser,
            plugin_registry=plugin_registry,
            capability_registry=capability_registry,
            event_bus=event_bus,
            learned_ranking=learned_ranking,
        )

        self._window: PanelWindow | None = None
        self._renderer: PanelRenderer | None = None
        self._bridge: _QtBridge | None = None

        # Import here to avoid circular imports at module level.
        # window_detector is the singleton created at the bottom of
        # context/window_detector.py — the same instance the orchestrator uses.
        from context.window_detector import window_detector as _wd

        self._hotkey = HotkeyListener(
            hotkey_str=self._config.hotkey,
            event_bus=event_bus,
            window_detector=_wd,
            panel_state=self._state,
        )

        self._last_result: Any = None

    # ------------------------------------------------------------------
    # Lifecycle
    # ------------------------------------------------------------------

    def start(self, loop: asyncio.AbstractEventLoop | None = None) -> None:
        """Initialise all subsystems. Must be called from the Qt thread."""
        import threading
        self._loop = loop or asyncio.new_event_loop()

        self._loop_thread: threading.Thread | None = None
        if not self._loop.is_running():
            self._loop_thread = threading.Thread(
                target=self._loop.run_forever,
                name="panel-asyncio",
                daemon=True,
            )
            self._loop_thread.start()

        future = asyncio.run_coroutine_threadsafe(self._history.open(), self._loop)
        future.result(timeout=10.0)

        # ── Create bridge on Qt thread ────────────────────────────────────
        self._bridge = _QtBridge()

        # ── Build UI ──────────────────────────────────────────────────────
        tokens = self._config.theme_tokens
        self._window = PanelWindow(tokens=tokens, state=self._state)
        self._renderer = PanelRenderer(
            tokens=tokens,
            all_themes=self._config.all_themes(),
            parent=None,
        )
        self._window.set_body(self._renderer)

        # ── Wire bridge → renderer/window ─────────────────────────────────
        self._bridge.sig_set_app_context.connect(self._renderer.set_app_context)
        self._bridge.sig_set_status.connect(self._renderer.set_status)
        self._bridge.sig_push_history.connect(self._renderer.push_history_item)
        self._bridge.sig_set_resolved_intent.connect(self._renderer.set_resolved_intent)
        self._bridge.sig_update_suggestions.connect(self._renderer.update_suggestions)
        self._bridge.sig_set_active_mode.connect(self._renderer.set_active_mode)
        self._bridge.sig_toggle.connect(self._window.toggle)
        self._bridge.sig_apply_theme.connect(self._apply_current_theme)

        if _HAS_QT:
            from PyQt6.QtWidgets import QApplication
            app = QApplication.instance()
            if app is not None:
                self._bridge.sig_request_quit.connect(app.quit)

        # ── Wire renderer → controller ────────────────────────────────────
        self._renderer.query_submitted.connect(self._on_query_submitted)
        self._renderer.setting_changed.connect(self._on_setting_changed)
        self._renderer.rerun_requested.connect(self._on_rerun)
        self._renderer.mode_change_requested.connect(self._on_mode_change_requested)

        # ── Pre-populate UI ───────────────────────────────────────────────
        self._renderer.set_theme_selection(self._state.theme)
        self._renderer.set_opacity_value(self._state.opacity)
        self._renderer.set_font_size_value(self._state.font_size)
        self._renderer.set_hotkey_value(self._state.hotkey)

        try:
            from core.mode_manager import mode_manager
            self._renderer.set_active_mode(mode_manager.current_mode.value)
        except Exception:
            pass

        self._renderer.set_suggest_callback(self._schedule_suggest)
        self._renderer.load_snippets(self._snippets.all())

        self._subscribe()
        self._hotkey.start()

        if self._window:
            self._window.show_panel()

        log.info("panel_controller: started (theme=%s)", self._state.theme)

    def stop(self) -> None:
        """Persist state and tear down."""
        self._hotkey.stop()
        self._state.save()

        try:
            if not self._loop.is_closed():
                future = asyncio.run_coroutine_threadsafe(self._history.close(), self._loop)
                try:
                    future.result(timeout=5.0)
                except Exception as exc:
                    log.warning("panel_controller: history close error — %s", exc)
                if getattr(self, "_loop_thread", None) is not None:
                    self._loop.call_soon_threadsafe(self._loop.stop)
        except Exception as exc:
            log.warning("panel_controller: stop error — %s", exc)

        if self._bridge is not None:
            try:
                self._bridge.sig_request_quit.emit()
            except Exception as exc:
                log.debug("panel_controller: sig_request_quit failed — %s", exc)

        log.info("panel_controller: stopped.")

    # ------------------------------------------------------------------
    # EventBus subscriptions
    # ------------------------------------------------------------------

    def _subscribe(self) -> None:
        handlers = {
            "panel_toggle_requested": self._on_toggle,
            "action_completed":       self._on_action_completed,
            "app_context_changed":    self._on_app_context_changed,
            "config_changed":         self._on_config_changed,
            "input_mode_changed":     self._on_input_mode_changed,
        }
        for event, handler in handlers.items():
            try:
                self._bus.subscribe(event, handler)
            except Exception as exc:
                log.warning("panel_controller: could not subscribe to '%s' — %s", event, exc)

    # ------------------------------------------------------------------
    # EventBus handlers  (fire on asyncio thread — use bridge signals)
    # ------------------------------------------------------------------

    def _on_toggle(self, _payload: Any) -> None:
        if self._bridge:
            self._bridge.sig_toggle.emit()

    def _on_action_completed(self, event: Any) -> None:
        payload = event.data if hasattr(event, "data") else event
        if not isinstance(payload, dict):
            payload = {}
        success  = bool(payload.get("success", False))
        duration = int(payload.get("duration_ms", 0))
        method   = payload.get("method", "unknown")
        query    = payload.get("query", "")
        intent   = payload.get("intent", None)

        if self._pending_row_id is not None:
            asyncio.run_coroutine_threadsafe(
                self._history.update_outcome(
                    self._pending_row_id,
                    success=success,
                    duration_ms=duration,
                    intent_resolved=intent,
                    method_used=method,
                ),
                self._loop,
            )
            self._pending_row_id = None

        if self._bridge and query:
            self._bridge.sig_push_history.emit(query, method, success)
            level = "success" if success else "error"
            msg   = f"Done ({method}, {duration} ms)" if success else "Failed"
            self._bridge.sig_set_status.emit(msg, level)

        if self._bridge and intent:
            self._bridge.sig_set_resolved_intent.emit(intent)

    def _on_app_context_changed(self, event: Any) -> None:
        payload = event.data if hasattr(event, "data") else event
        if not isinstance(payload, dict):
            payload = {}
        app = payload.get("app_name", "unknown")
        self._engine.set_app_context(app)
        if self._bridge:
            self._bridge.sig_set_app_context.emit(app)

    def _on_config_changed(self, event: Any) -> None:
        payload = event.data if hasattr(event, "data") else event
        if not isinstance(payload, dict):
            payload = {}
        if payload.get("source") == "panel":
            return
        if self._bridge:
            self._bridge.sig_apply_theme.emit()

    def _on_input_mode_changed(self, event: Any) -> None:
        """Reflect external mode changes (dashboard) in the panel switcher."""
        payload = event.data if hasattr(event, "data") else event
        if not isinstance(payload, dict):
            payload = {}
        new_mode = payload.get("new_mode", "")
        if self._bridge and new_mode:
            self._bridge.sig_set_active_mode.emit(new_mode)

    # ------------------------------------------------------------------
    # Renderer signal handlers  (Qt thread — direct calls are safe)
    # ------------------------------------------------------------------

    def _on_mode_change_requested(self, mode_str: str) -> None:
        """User clicked a mode button — publish to bus for mode_manager."""
        log.info("panel_controller: mode switch requested → %s", mode_str)
        try:
            self._bus.publish(
                "panel_mode_switch_requested",
                {"mode": mode_str},
                source="panel",
            )
        except Exception as exc:
            log.error("panel_controller: failed to publish mode switch — %s", exc)

    def _on_query_submitted(self, query: str, chosen_method: str) -> None:
        if self._last_result and self._last_result.top:
            default_method = self._last_result.top.method
            intent = self._last_result.intent
            self._engine.publish_override(query, chosen_method, default_method, intent)

        self._pending_start_ms = time.monotonic() * 1000
        future = asyncio.run_coroutine_threadsafe(
            self._history.record(
                query_text=query,
                app_context=self._engine._current_app,
                limit=self._state.history_limit,
            ),
            self._loop,
        )
        try:
            self._pending_row_id = future.result(timeout=5.0)
        except Exception as exc:
            log.warning("panel_controller: history.record failed — %s", exc)
            self._pending_row_id = None

        # Build the query payload. Inject the pre-panel context captured by
        # HotkeyListener so the orchestrator receives the user's real cwd
        # rather than re-snapshotting when the panel window is already active.
        payload: dict[str, Any] = {
            "query":            query,
            "source":           "panel",
            "preferred_method": chosen_method,
        }

        # Priority for context resolution:
        #   1. pre_panel_context from HotkeyListener (hotkey-triggered open)
        #   2. _last_real_context from window_detector — the window that was
        #      active just before focus switched to VS Code/panel. Updated
        #      in real time by xprop, immune to panel-click fake focus events
        #      because we save BEFORE each focus switch, not after.
        pre_context = self._state.pre_panel_context
        if pre_context is None:
            try:
                from context.window_detector import window_detector as _wd_ref
                pre_context = _wd_ref._last_real_context
                if pre_context is not None:
                    log.debug(
                        "panel_controller: using _last_real_context cwd=%r window=%r",
                        pre_context.get("cwd"),
                        pre_context.get("window_title"),
                    )
            except Exception as exc:
                log.warning("panel_controller: could not read _last_real_context — %s", exc)

        if pre_context is not None:
            payload["cwd"]               = pre_context.get("cwd")
            payload["pre_panel_context"] = pre_context
            log.debug(
                "panel_controller: injecting pre-panel cwd=%r into query payload",
                pre_context.get("cwd"),
            )

        try:
            self._bus.publish("text_query_received", payload)
        except Exception as exc:
            log.error("panel_controller: failed to publish query — %s", exc)

        if self._renderer:
            self._renderer.set_status("Running…", "info")

    def _on_setting_changed(self, key: str, value: Any) -> None:
        self._config.update(**{key: value})
        if key == "theme":
            self._apply_current_theme()
        elif key == "opacity" and self._window:
            self._window.apply_opacity(float(value))
        elif key == "hotkey":
            self._hotkey.update_hotkey(str(value))
        elif key in ("font_size", "font_family"):
            self._apply_current_theme()

    def _on_rerun(self, query: str) -> None:
        """Re-run a history item, preserving its original execution method."""
        import re
        method = "command"  # safe default
        if self._renderer is not None:
            hist = getattr(self._renderer, "_history_list", None)
            if hist is not None:
                item = hist.currentItem()
                if item:
                    # History items are formatted as: "✓  [shell]  git status"
                    m = re.search(r'\[(\w+)\]', item.text())
                    if m and m.group(1) in ("plugin", "api", "command", "shell", "ui"):
                        method = m.group(1)
        self._on_query_submitted(query, method)

    # ------------------------------------------------------------------
    # Suggestion pipeline
    # ------------------------------------------------------------------

    def _schedule_suggest(self, text: str) -> None:
        asyncio.run_coroutine_threadsafe(self._async_suggest(text), self._loop)

    async def _async_suggest(self, text: str) -> None:
        try:
            result = await self._engine.suggest(text)
            self._last_result = result
            if self._bridge:
                self._bridge.sig_update_suggestions.emit(result)
        except Exception as exc:
            log.warning("panel_controller: suggestion failed — %s", exc)

    # ------------------------------------------------------------------
    # Theme helpers  (called on Qt thread via bridge signal)
    # ------------------------------------------------------------------

    def _apply_current_theme(self) -> None:
        tokens = self._config.theme_tokens
        if self._window:
            self._window.apply_tokens(tokens)
        if self._renderer:
            self._renderer.set_tokens(tokens)
        log.info("panel_controller: theme applied (%s)", self._state.theme)