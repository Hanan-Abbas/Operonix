"""
panel/panel_state.py

Single source of truth for every panel preference.
Serialised to ~/.operonix/panel_state.json and loaded on startup.
No hardcoded defaults live anywhere else — change them here and
the entire panel inherits the new values automatically.
"""
from __future__ import annotations

import json
import logging
from dataclasses import asdict, dataclass, field
from pathlib import Path
from typing import Any

log = logging.getLogger(__name__)

_STATE_PATH = Path.home() / ".operonix" / "panel_state.json"

# ---------------------------------------------------------------------------
# Available built-in themes (name → display label).
# Users can also add custom themes via panel_theme.py.
# ---------------------------------------------------------------------------
BUILTIN_THEMES: dict[str, str] = {
    "auto":     "Auto (follow OS)",
    "dark":     "Dark",
    "light":    "Light",
    "midnight": "Midnight Blue",
    "solarized_dark":  "Solarized Dark",
    "solarized_light": "Solarized Light",
    "dracula":  "Dracula",
    "nord":     "Nord",
    "gruvbox":  "Gruvbox",
    "monokai":  "Monokai",
    "catppuccin_mocha":  "Catppuccin Mocha",
    "catppuccin_latte":  "Catppuccin Latte",
}


@dataclass
class PanelState:
    """All panel preferences in one place."""

    # --- geometry ---
    x: int = 100
    y: int = 100
    width: int = 520
    height: int = 300

    # --- appearance ---
    opacity: float = 0.96
    theme: str = "auto"                # must be a key in BUILTIN_THEMES or a custom name
    font_size: int = 13                # base UI font size in pt
    font_family: str = "JetBrains Mono, Fira Code, monospace"

    # --- behaviour ---
    pinned: bool = False               # stays visible when Operonix is idle
    active_tab: str = "command"        # "command" | "history" | "snippets" | "settings"
    history_limit: int = 200
    debounce_ms: int = 250             # suggestion debounce in milliseconds
    hotkey: str = "<ctrl>+<space>"     # pynput-compatible global hotkey

    # --- custom theme registry (name → token dict) ---
    custom_themes: dict[str, dict[str, Any]] = field(default_factory=dict)

    # ------------------------------------------------------------------
    # Persistence
    # ------------------------------------------------------------------

    @classmethod
    def load(cls) -> "PanelState":
        """Load state from disk, falling back to defaults on any error."""
        try:
            if _STATE_PATH.exists():
                raw = json.loads(_STATE_PATH.read_text(encoding="utf-8"))
                # Only apply keys that actually exist on the dataclass so
                # old or unknown keys never crash the load.
                valid = {k: v for k, v in raw.items() if k in cls.__dataclass_fields__}
                return cls(**valid)
        except Exception as exc:  # noqa: BLE001
            log.warning("panel_state: failed to load %s — %s. Using defaults.", _STATE_PATH, exc)
        return cls()

    def save(self) -> None:
        """Persist current state to disk atomically."""
        try:
            _STATE_PATH.parent.mkdir(parents=True, exist_ok=True)
            tmp = _STATE_PATH.with_suffix(".tmp")
            tmp.write_text(json.dumps(asdict(self), indent=2), encoding="utf-8")
            tmp.replace(_STATE_PATH)
            log.debug("panel_state: saved to %s", _STATE_PATH)
        except Exception as exc:  # noqa: BLE001
            log.error("panel_state: failed to save — %s", exc)

    def update(self, **kwargs: Any) -> None:
        """Apply a partial update and immediately persist."""
        for key, value in kwargs.items():
            if key in self.__dataclass_fields__:
                setattr(self, key, value)
            else:
                log.warning("panel_state: unknown field '%s' ignored.", key)
        self.save()

    def register_custom_theme(self, name: str, tokens: dict[str, Any]) -> None:
        """Register a user-defined theme token dict and persist."""
        self.custom_themes[name] = tokens
        self.save()
        log.info("panel_state: custom theme '%s' registered.", name)

    def all_theme_names(self) -> dict[str, str]:
        """Return all available theme keys → display labels."""
        combined = dict(BUILTIN_THEMES)
        for name in self.custom_themes:
            combined[name] = f"{name} (custom)"
        return combined