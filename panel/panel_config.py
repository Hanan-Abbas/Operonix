"""
panel/panel_config.py

Thin configuration wrapper that bridges:
  • core/config.py  (global Operonix Settings)
  • panel/panel_state.py  (panel-specific preferences)

Responsibilities:
  - Load/expose merged config at startup
  - Emit 'config_changed' on the EventBus when prefs change
  - Never hardcode any value; every default lives in PanelState or Settings
"""
from __future__ import annotations

import logging
from typing import Any

from panel.panel_state import PanelState
from panel.panel_theme import ThemeTokens, resolve_theme

log = logging.getLogger(__name__)


class PanelConfig:
    """
    Single entry point for all panel configuration.

    Usage::

        cfg = PanelConfig(event_bus)
        tokens = cfg.theme_tokens          # current ThemeTokens
        cfg.update(theme="nord")           # persists + fires EventBus event
    """

    def __init__(self, event_bus: Any) -> None:
        self._bus = event_bus
        self._state = PanelState.load()
        log.info("panel_config: loaded state (theme=%s, hotkey=%s)",
                 self._state.theme, self._state.hotkey)

    # ------------------------------------------------------------------
    # Accessors
    # ------------------------------------------------------------------

    @property
    def state(self) -> PanelState:
        return self._state

    @property
    def theme_tokens(self) -> ThemeTokens:
        return resolve_theme(
            self._state.theme,
            custom_themes=self._state.custom_themes,
            font_family=self._state.font_family,
            font_size=self._state.font_size,
        )

    @property
    def hotkey(self) -> str:
        return self._state.hotkey

    @property
    def debounce_ms(self) -> int:
        return self._state.debounce_ms

    # ------------------------------------------------------------------
    # Mutation — always persists + fires event
    # ------------------------------------------------------------------

    def update(self, **kwargs: Any) -> None:
        """
        Apply a partial settings update.
        Fires 'config_changed' on the EventBus with the changed keys.
        """
        self._state.update(**kwargs)
        self._publish_change(list(kwargs.keys()))
        log.info("panel_config: updated %s", list(kwargs.keys()))

    def register_custom_theme(self, name: str, tokens: dict[str, Any]) -> None:
        """Register a user-defined theme and broadcast the change."""
        self._state.register_custom_theme(name, tokens)
        self._publish_change(["custom_themes", "theme_registry"])

    def all_themes(self) -> dict[str, str]:
        """Return all available theme keys → display labels."""
        return self._state.all_theme_names()

    # ------------------------------------------------------------------
    # Internal
    # ------------------------------------------------------------------

    def _publish_change(self, changed_keys: list[str]) -> None:
        try:
            self._bus.publish(
                "config_changed",
                {"source": "panel", "changed_keys": changed_keys},
            )
        except Exception as exc:  # noqa: BLE001
            log.warning("panel_config: could not publish config_changed — %s", exc)