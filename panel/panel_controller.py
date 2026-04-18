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

    