"""
panel/panel_controller.py

The critical wiring file.
Connects the panel UI ↔ EventBus.  It:

  • Publishes text_query_received (same event the voice pipeline fires)
  • Publishes panel_toggle_requested / execution_strategy_overridden
  • Subscribes to action_completed, app_context_changed, config_changed

The controller never imports concrete brain/, capabilities/, or tools/ code.
All subsystem interactions go through the EventBus.

FIX CHANGELOG (Step 1):
  • _on_app_context_changed and _on_action_completed now post Qt widget
    updates via QMetaObject.invokeMethod(Qt.ConnectionType.QueuedConnection)
    instead of calling renderer methods directly. The EventBus callbacks
    fire on the asyncio thread; calling Qt widget methods from a non-Qt
    thread was the root cause of the segfault on mode switch.
  • stop() no longer calls self._window.hide() directly from the executor
    thread. It instead calls QApplication.quit() via invokeMethod, which
    causes qt_app.exec() in the panel thread to return cleanly. This is
    the correct Qt-safe shutdown path.
  • start() signature unchanged — loop=None continues to work as before,
    causing the controller to create and own its own asyncio loop on a
    dedicated "panel-asyncio" daemon thread.
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


def _invoke_on_qt_thread(obj: Any, method_name: str, *args: Any) -> None:
    """
    Schedule a call to obj.method_name(*args) on the Qt GUI thread.

    Uses QMetaObject.invokeMethod with QueuedConnection so it is safe to
    call from any thread (asyncio thread, executor thread, etc.).
    If PyQt6 is not available or obj is None this is a no-op.
    """
    if obj is None:
        return
    try:
        from PyQt6.QtCore import QMetaObject, Qt, Q_ARG
        from PyQt6.QtCore import QObject
        # For methods with no arguments the simple string overload works.
        if not args:
            QMetaObject.invokeMethod(
                obj,
                method_name,
                Qt.ConnectionType.QueuedConnection,
            )
        else:
            # PyQt6 does not expose Q_ARG as a Python-callable in all builds;
            # fall back to a zero-arg lambda posted via a QTimer when there
            # are arguments, which is always safe from any thread.
            from PyQt6.QtCore import QTimer
            # Capture args in closure to avoid late-binding issues.
            captured = args
            def _call():
                getattr(obj, method_name)(*captured)
            QTimer.singleShot(0, obj, _call)
    except Exception as exc:  # noqa: BLE001
        log.debug("_invoke_on_qt_thread: could not post %s.%s — %s", obj, method_name, exc)


class PanelController:
    """
    Owns all panel subsystems and wires them together.

    Lifecycle::

        ctrl = PanelController(event_bus, intent_parser, plugin_registry,
                               capability_registry, learned_ranking)
        ctrl.start()          # creates Qt window, registers hotkey
        # ... application runs ...
        ctrl.stop()           # persists state, tears down

    Args:
        event_bus:           Operonix EventBus instance.
        intent_parser:       Callable[str] -> dict (brain/intent_parser.py wrapper).
        plugin_registry:     Callable[str, str] -> list[dict].
        capability_registry: Callable[str] -> list[dict].
        learned_ranking:     Optional Callable[str, str] -> list[str].
        qt_app:              Optional QApplication (if already created by caller).
    """

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

        # Config & state
        self._config = PanelConfig(event_bus)
        self._state = self._config.state

        # Stores
        self._history = HistoryStore()
        self._snippets = SnippetStore()

        # Suggestion engine
        self._engine = SuggestionEngine(
            intent_parser=intent_parser,
            plugin_registry=plugin_registry,
            capability_registry=capability_registry,
            event_bus=event_bus,
            learned_ranking=learned_ranking,
        )

        # UI
        self._window: PanelWindow | None = None
        self._renderer: PanelRenderer | None = None

        # Hotkey listener
        self._hotkey = HotkeyListener(
            hotkey_str=self._config.hotkey,
            event_bus=event_bus,
        )

        # Track the last suggestion result for override reporting.
        self._last_result: Any = None

    # ------------------------------------------------------------------
    # Lifecycle
    # ------------------------------------------------------------------

    def start(self, loop: asyncio.AbstractEventLoop | None = None) -> None:
        """Initialise all subsystems. Call from the main Qt thread."""
        import threading
        self._loop = loop or asyncio.new_event_loop()

        # The asyncio loop must be RUNNING (via run_forever) so that
        # asyncio.run_coroutine_threadsafe can submit coroutines from Qt
        # signal handlers.  We spin it in a daemon thread so it doesn't
        # block Qt's exec().  If the caller already passed in a running loop
        # (e.g. the orchestrator's panel thread), we skip this step.
        self._loop_thread: threading.Thread | None = None
        if not self._loop.is_running():
            self._loop_thread = threading.Thread(
                target=self._loop.run_forever,
                name="panel-asyncio",
                daemon=True,
            )
            self._loop_thread.start()

        # Open history DB — submit as a coroutine to the now-running loop.
        future = asyncio.run_coroutine_threadsafe(self._history.open(), self._loop)
        future.result(timeout=10.0)  # block until open() completes

        # Build UI
        tokens = self._config.theme_tokens
        self._window = PanelWindow(tokens=tokens, state=self._state)
        self._renderer = PanelRenderer(
            tokens=tokens,
            all_themes=self._config.all_themes(),
            parent=None,
        )
        self._window.set_body(self._renderer)

        # Wire renderer signals → controller methods.
        self._renderer.query_submitted.connect(self._on_query_submitted)
        self._renderer.setting_changed.connect(self._on_setting_changed)
        self._renderer.rerun_requested.connect(self._on_rerun)

        # Pre-populate UI state.
        self._renderer.set_theme_selection(self._state.theme)
        self._renderer.set_opacity_value(self._state.opacity)
        self._renderer.set_font_size_value(self._state.font_size)
        self._renderer.set_hotkey_value(self._state.hotkey)

        # Register suggestion callback with the renderer.
        self._renderer.set_suggest_callback(self._schedule_suggest)

        # Load snippets.
        self._renderer.load_snippets(self._snippets.all())

        # Subscribe to EventBus events.
        self._subscribe()

        # Start hotkey listener.
        self._hotkey.start()

        # Show the window.
        if self._window:
            self._window.show_panel()

        log.info("panel_controller: started (theme=%s)", self._state.theme)

    def stop(self) -> None:
        """
        Persist state and tear down.

        FIX: Previously called self._window.hide() directly, which runs on
        whatever thread calls stop() (usually a thread-pool executor thread).
        Calling any Qt widget method from a non-Qt thread is undefined
        behaviour and was a contributing factor to the segfault on mode switch.

        The correct shutdown sequence is:
          1. Stop the hotkey listener (pure Python, thread-safe).
          2. Persist state to disk (pure Python, thread-safe).
          3. Close the history DB via the asyncio loop we own.
          4. Stop our asyncio loop (only if we started it).
          5. Post QApplication.quit() onto the Qt thread — this causes
             qt_app.exec() in the panel thread to return, which naturally
             destroys all widgets on the correct thread.
        """
        self._hotkey.stop()
        self._state.save()

        try:
            if not self._loop.is_closed():
                future = asyncio.run_coroutine_threadsafe(self._history.close(), self._loop)
                try:
                    future.result(timeout=5.0)
                except Exception as exc:  # noqa: BLE001
                    log.warning("panel_controller: history close error — %s", exc)

                # Stop our asyncio loop only if we started it.
                if getattr(self, "_loop_thread", None) is not None:
                    self._loop.call_soon_threadsafe(self._loop.stop)

        except Exception as exc:  # noqa: BLE001
            log.warning("panel_controller: stop error — %s", exc)

        # Post QApplication.quit() onto the Qt thread safely.
        # This causes qt_app.exec() to return, destroying all widgets
        # on the correct thread without any cross-thread widget access.
        try:
            from PyQt6.QtWidgets import QApplication
            app = QApplication.instance()
            if app is not None:
                _invoke_on_qt_thread(app, "quit")
        except Exception as exc:  # noqa: BLE001
            log.debug("panel_controller: could not post quit — %s", exc)

        log.info("panel_controller: stopped.")

    # ------------------------------------------------------------------
    # EventBus subscriptions
    # ------------------------------------------------------------------

    def _subscribe(self) -> None:
        handlers = {
            "panel_toggle_requested":  self._on_toggle,
            "action_completed":        self._on_action_completed,
            "app_context_changed":     self._on_app_context_changed,
            "config_changed":          self._on_config_changed,
        }
        for event, handler in handlers.items():
            try:
                self._bus.subscribe(event, handler)
            except Exception as exc:  # noqa: BLE001
                log.warning("panel_controller: could not subscribe to '%s' — %s", event, exc)

    # ------------------------------------------------------------------
    # EventBus handlers
    # ------------------------------------------------------------------

    def _on_toggle(self, _payload: Any) -> None:
        # window.toggle() is a Qt call — post it onto the Qt thread.
        if self._window:
            _invoke_on_qt_thread(self._window, "toggle")

    def _on_action_completed(self, event: Any) -> None:
        # Accept both an Event object and a plain dict for resilience.
        payload = event.data if hasattr(event, "data") else event
        if not isinstance(payload, dict):
            payload = {}
        success = bool(payload.get("success", False))
        duration = int(payload.get("duration_ms", 0))
        method = payload.get("method", "unknown")
        query = payload.get("query", "")
        intent = payload.get("intent", None)

        # Update history row (asyncio-safe — runs on the panel-asyncio thread).
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

        # Update UI — post onto the Qt thread to avoid cross-thread widget access.
        if self._renderer and query:
            _invoke_on_qt_thread(self._renderer, "push_history_item", query, method, success)
            level = "success" if success else "error"
            msg = f"Done ({method}, {duration} ms)" if success else "Failed"
            _invoke_on_qt_thread(self._renderer, "set_status", msg, level)

        if self._renderer and intent:
            _invoke_on_qt_thread(self._renderer, "set_resolved_intent", intent)

        log.debug("panel_controller: action_completed success=%s method=%s", success, method)

    def _on_app_context_changed(self, event: Any) -> None:
        payload = event.data if hasattr(event, "data") else event
        if not isinstance(payload, dict):
            payload = {}
        app = payload.get("app_name", "unknown")
        self._engine.set_app_context(app)

        # FIX: Previously called self._renderer.set_app_context(app) directly.
        # This callback fires on the asyncio thread (not the Qt thread), so any
        # direct Qt widget call here is a cross-thread violation → segfault.
        # Post it onto the Qt thread via QTimer.singleShot (wrapped in
        # _invoke_on_qt_thread) so the widget update always happens safely.
        if self._renderer:
            _invoke_on_qt_thread(self._renderer, "set_app_context", app)

        log.debug("panel_controller: app context → %s", app)

    def _on_config_changed(self, event: Any) -> None:
        payload = event.data if hasattr(event, "data") else event
        if not isinstance(payload, dict):
            payload = {}
        if payload.get("source") == "panel":
            return  # Ignore changes we ourselves fired.
        # Re-read tokens in case an external config change affects the theme.
        # _apply_current_theme touches Qt widgets — post onto Qt thread.
        _invoke_on_qt_thread(
            self._window,   # any QObject in the Qt thread works as the receiver
            "_apply_theme_slot",
        ) if self._window else self._apply_current_theme()

    # ------------------------------------------------------------------
    # Renderer signal handlers (already on Qt thread — no invokeMethod needed)
    # ------------------------------------------------------------------

    def _on_query_submitted(self, query: str, chosen_method: str) -> None:
        # Report override if the user changed the default strategy.
        if self._last_result and self._last_result.top:
            default_method = self._last_result.top.method
            intent = self._last_result.intent
            self._engine.publish_override(query, chosen_method, default_method, intent)

        # Persist to history (row updated when action_completed fires).
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
        except Exception as exc:  # noqa: BLE001
            log.warning("panel_controller: history.record failed — %s", exc)
            self._pending_row_id = None

        # Publish to the Orchestrator (same event as voice pipeline).
        try:
            self._bus.publish(
                "text_query_received",
                {
                    "query": query,
                    "source": "panel",
                    "preferred_method": chosen_method,
                },
            )
            log.info("panel_controller: published text_query_received '%s' (method=%s)",
                     query, chosen_method)
        except Exception as exc:  # noqa: BLE001
            log.error("panel_controller: failed to publish query — %s", exc)

        if self._renderer:
            self._renderer.set_status("Running…", "info")

    def _on_setting_changed(self, key: str, value: Any) -> None:
        log.debug("panel_controller: setting changed %s=%s", key, value)
        self._config.update(**{key: value})

        if key == "theme":
            self._apply_current_theme()
        elif key == "opacity" and self._window:
            self._window.apply_opacity(float(value))
        elif key == "hotkey":
            self._hotkey.update_hotkey(str(value))
        elif key in ("font_size", "font_family"):
            self._apply_current_theme()  # Tokens include font — full rebuild.

    def _on_rerun(self, query: str) -> None:
        # Treat as a fresh submission with the default method.
        self._on_query_submitted(query, "command")

    # ------------------------------------------------------------------
    # Suggestion pipeline
    # ------------------------------------------------------------------

    def _schedule_suggest(self, text: str) -> None:
        """
        Called by the renderer's debounce timer (Qt thread) to schedule the
        async suggest pipeline.
        """
        asyncio.run_coroutine_threadsafe(self._async_suggest(text), self._loop)

    async def _async_suggest(self, text: str) -> None:
        try:
            result = await self._engine.suggest(text)
            self._last_result = result
            if self._renderer:
                _invoke_on_qt_thread(self._renderer, "update_suggestions", result)
        except Exception as exc:  # noqa: BLE001
            log.warning("panel_controller: suggestion failed — %s", exc)

    # ------------------------------------------------------------------
    # Theme helpers
    # ------------------------------------------------------------------

    def _apply_current_theme(self) -> None:
        """Must be called from the Qt thread."""
        tokens = self._config.theme_tokens
        if self._window:
            self._window.apply_tokens(tokens)
        if self._renderer:
            self._renderer.set_tokens(tokens)
        log.info("panel_controller: theme applied (%s)", self._state.theme)