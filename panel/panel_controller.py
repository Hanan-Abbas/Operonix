"""
panel/panel_controller.py

The critical wiring file.
Connects the panel UI ↔ EventBus.  It:

  • Publishes text_query_received (same event the voice pipeline fires)
  • Publishes panel_toggle_requested / execution_strategy_overridden
  • Subscribes to action_completed, app_context_changed, config_changed

The controller never imports concrete brain/, capabilities/, or tools/ code.
All subsystem interactions go through the EventBus.
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
        self._loop = loop or asyncio.new_event_loop()

        # Open history DB
        self._loop.run_until_complete(self._history.open())

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
        """Persist state and tear down."""
        self._hotkey.stop()
        self._state.save()
        # history.close() is a coroutine — run it on our dedicated loop.
        # Guard against the case where the loop is already closed or running.
        try:
            if not self._loop.is_closed():
                if self._loop.is_running():
                    # Schedule it as a future; don't block — we're shutting down.
                    asyncio.run_coroutine_threadsafe(self._history.close(), self._loop)
                else:
                    self._loop.run_until_complete(self._history.close())
        except Exception as exc:  # noqa: BLE001
            log.warning("panel_controller: history close error — %s", exc)
        if self._window:
            try:
                self._window.hide()
            except Exception:  # noqa: BLE001
                pass
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
        if self._window:
            self._window.toggle()

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

        # Update history row.
        if self._pending_row_id is not None:
            if not self._loop.is_running():
                self._loop.run_until_complete(
                    self._history.update_outcome(
                        self._pending_row_id,
                        success=success,
                        duration_ms=duration,
                        intent_resolved=intent,
                        method_used=method,
                    )
                )
            self._pending_row_id = None

        # Update UI.
        if self._renderer and query:
            self._renderer.push_history_item(query, method, success)
            level = "success" if success else "error"
            msg = f"Done ({method}, {duration} ms)" if success else "Failed"
            self._renderer.set_status(msg, level)

        log.debug("panel_controller: action_completed success=%s method=%s", success, method)

    def _on_app_context_changed(self, event: Any) -> None:
        payload = event.data if hasattr(event, "data") else event
        if not isinstance(payload, dict):
            payload = {}
        app = payload.get("app_name", "unknown")
        self._engine.set_app_context(app)
        # Immediately reflect the new active window in the panel badge.
        # Previously this was never called here, so the badge only updated
        # when update_suggestions() ran (i.e. when the user typed something).
        if self._renderer:
            self._renderer.set_app_context(app)
        log.debug("panel_controller: app context → %s", app)

    def _on_config_changed(self, event: Any) -> None:
        payload = event.data if hasattr(event, "data") else event
        if not isinstance(payload, dict):
            payload = {}
        if payload.get("source") == "panel":
            return  # Ignore changes we ourselves fired.
        # Re-read tokens in case an external config change affects the theme.
        self._apply_current_theme()

    # ------------------------------------------------------------------
    # Renderer signal handlers
    # ------------------------------------------------------------------

    def _on_query_submitted(self, query: str, chosen_method: str) -> None:
        # Report override if the user changed the default strategy.
        if self._last_result and self._last_result.top:
            default_method = self._last_result.top.method
            intent = self._last_result.intent
            self._engine.publish_override(query, chosen_method, default_method, intent)

        # Persist to history (row updated when action_completed fires).
        self._pending_start_ms = time.monotonic() * 1000
        if not self._loop.is_running():
            self._pending_row_id = self._loop.run_until_complete(
                self._history.record(
                    query_text=query,
                    app_context=self._engine._current_app,
                    limit=self._state.history_limit,
                )
            )

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
        """Called by the renderer's debounce timer; schedules async suggest."""
        if self._loop.is_running():
            asyncio.ensure_future(self._async_suggest(text), loop=self._loop)
        else:
            self._loop.run_until_complete(self._async_suggest(text))

    async def _async_suggest(self, text: str) -> None:
        try:
            result = await self._engine.suggest(text)
            self._last_result = result
            if self._renderer:
                self._renderer.update_suggestions(result)
        except Exception as exc:  # noqa: BLE001
            log.warning("panel_controller: suggestion failed — %s", exc)

    # ------------------------------------------------------------------
    # Theme helpers
    # ------------------------------------------------------------------

    def _apply_current_theme(self) -> None:
        tokens = self._config.theme_tokens
        if self._window:
            self._window.apply_tokens(tokens)
        if self._renderer:
            self._renderer.set_tokens(tokens)
        log.info("panel_controller: theme applied (%s)", self._state.theme)