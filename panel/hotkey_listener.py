"""
panel/hotkey_listener.py

Registers a global hotkey that works across all applications.
Uses pynput; the key combination is read from PanelConfig (never hardcoded).

When triggered it publishes 'panel_toggle_requested' on the EventBus.
The panel_window.py subscribes and shows/hides itself.

The listener runs in a daemon thread so it never blocks the Qt event loop.
"""
from __future__ import annotations

import logging
import threading
from typing import Any

log = logging.getLogger(__name__)

try:
    from pynput import keyboard as _kb
    _HAS_PYNPUT = True
except ImportError:
    _HAS_PYNPUT = False
    log.warning("hotkey_listener: pynput not installed — global hotkey disabled.")


def _parse_hotkey(hotkey_str: str) -> set[Any]:
    """
    Parse a pynput-style hotkey string like '<ctrl>+<space>' into
    the set of Key / KeyCode objects pynput expects.
    """
    if not _HAS_PYNPUT:
        return set()
    parts = [p.strip() for p in hotkey_str.split("+")]
    keys: set[Any] = set()
    for part in parts:
        if part.startswith("<") and part.endswith(">"):
            key_name = part[1:-1]
            try:
                keys.add(getattr(_kb.Key, key_name))
            except AttributeError:
                log.warning("hotkey_listener: unknown key '%s' in hotkey '%s'", part, hotkey_str)
        else:
            keys.add(_kb.KeyCode.from_char(part))
    return keys


class HotkeyListener:
    """
    Listens for a configurable global hotkey and fires an EventBus event.

    Args:
        hotkey_str: pynput-style string, e.g. '<ctrl>+<space>'
        event_bus:  EventBus instance
    """

    def __init__(self, hotkey_str: str, event_bus: Any) -> None:
        self._hotkey_str = hotkey_str
        self._bus = event_bus
        self._target_keys: set[Any] = _parse_hotkey(hotkey_str)
        self._pressed: set[Any] = set()
        self._listener: Any = None
        self._lock = threading.Lock()

    # ------------------------------------------------------------------
    # Lifecycle
    # ------------------------------------------------------------------

    def start(self) -> None:
        if not _HAS_PYNPUT or not self._target_keys:
            log.info("hotkey_listener: not started (pynput unavailable or empty hotkey).")
            return
        self._listener = _kb.Listener(
            on_press=self._on_press,
            on_release=self._on_release,
            daemon=True,
        )
        self._listener.start()
        log.info("hotkey_listener: registered global hotkey '%s'", self._hotkey_str)

    def stop(self) -> None:
        if self._listener:
            try:
                self._listener.stop()
            except Exception as exc:  # noqa: BLE001
                log.debug("hotkey_listener: stop error — %s", exc)
            self._listener = None
        log.info("hotkey_listener: stopped.")

    def update_hotkey(self, new_hotkey_str: str) -> None:
        """Swap the hotkey at runtime without restarting the listener."""
        self.stop()
        self._hotkey_str = new_hotkey_str
        self._target_keys = _parse_hotkey(new_hotkey_str)
        self._pressed.clear()
        self.start()
        log.info("hotkey_listener: updated hotkey to '%s'", new_hotkey_str)

    # ------------------------------------------------------------------
    # Internal key tracking
    # ------------------------------------------------------------------

    def _on_press(self, key: Any) -> None:
        with self._lock:
            self._pressed.add(self._normalise(key))
            if self._target_keys and self._target_keys.issubset(self._pressed):
                log.debug("hotkey_listener: hotkey triggered")
                self._fire()

    def _on_release(self, key: Any) -> None:
        with self._lock:
            self._pressed.discard(self._normalise(key))

    def _normalise(self, key: Any) -> Any:
        """Normalise KeyCode so char comparison works regardless of shift state."""
        if _HAS_PYNPUT and isinstance(key, _kb.KeyCode) and key.char is not None:
            return _kb.KeyCode.from_char(key.char.lower())
        return key

    def _fire(self) -> None:
        try:
            self._bus.publish("panel_toggle_requested", {"source": "hotkey"})
        except Exception as exc:  # noqa: BLE001
            log.warning("hotkey_listener: could not publish event — %s", exc)