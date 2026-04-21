"""
core/input_mode.py — Operonix AI OS Agent
══════════════════════════════════════════
Single source of truth for input mode definitions.

Imported by:
  • core/config.py         — adds CURRENT_MODE field to Settings
  • core/lifecycle_manager.py — set_mode() uses InputMode + ALLOWED_TRANSITIONS
  • api/routes/system.py   — validates incoming mode strings from the API

Nothing else should define or re-define these concepts.
"""
from __future__ import annotations

from enum import Enum


class InputMode(str, Enum):
    """The three valid input states Operonix can be in at any time."""

    NONE  = "none"   # Idle — no input subsystem running (boot default until .env sets it)
    VOICE = "voice"  # Voice pipeline active: wake-word, mic, STT, TTS
    PANEL = "panel"  # Panel UI active: Qt renderer, suggestion engine, history store


# ---------------------------------------------------------------------------
# Allowed transitions
# ---------------------------------------------------------------------------
# Maps current mode → set of modes it may transition TO.
# Any transition not listed here raises ModeTransitionError.
# This is the only place that defines valid mode paths — never inline these.

ALLOWED_TRANSITIONS: dict[InputMode, set[InputMode]] = {
    InputMode.NONE:  {InputMode.VOICE, InputMode.PANEL},
    InputMode.VOICE: {InputMode.PANEL, InputMode.NONE},
    InputMode.PANEL: {InputMode.VOICE, InputMode.NONE},
}


# ---------------------------------------------------------------------------
# Exceptions
# ---------------------------------------------------------------------------

class ModeTransitionError(RuntimeError):
    """
    Raised by lifecycle_manager.set_mode() when the requested transition is
    not in ALLOWED_TRANSITIONS.

    Example:
        VOICE → VOICE  (already in that mode — caller should check first)
    """

    def __init__(self, from_mode: InputMode, to_mode: InputMode) -> None:
        self.from_mode = from_mode
        self.to_mode   = to_mode
        super().__init__(
            f"Cannot transition from InputMode.{from_mode.name} "
            f"to InputMode.{to_mode.name}. "
            f"Allowed targets from {from_mode.name}: "
            f"{[m.name for m in ALLOWED_TRANSITIONS.get(from_mode, set())]}"
        )


# ---------------------------------------------------------------------------
# Helper
# ---------------------------------------------------------------------------

def parse_mode(raw: str) -> InputMode:
    """
    Convert a raw string (from .env or API payload) to InputMode.
    Raises ValueError with a clear message on invalid input.

    Usage:
        mode = parse_mode(os.getenv("CURRENT_MODE", "panel"))
    """
    try:
        return InputMode(raw.strip().lower())
    except ValueError:
        valid = [m.value for m in InputMode]
        raise ValueError(
            f"Invalid input mode {raw!r}. Must be one of: {valid}"
        )