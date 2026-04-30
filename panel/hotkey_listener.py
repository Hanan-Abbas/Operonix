"""
panel/hotkey_listener.py

Registers a global hotkey that works across all applications.
Uses pynput; the key combination is read from PanelConfig (never hardcoded).

When triggered it:
  1. Captures the current window context (title, pid, cwd) BEFORE the panel
     is shown — this is the only moment we know what the user was working in.
     The snapshot is stored in PanelState.pre_panel_context so that
     input_adapter can attach it to every query published while the panel
     is open.
  2. Publishes 'panel_toggle_requested' on the EventBus.

The listener runs in a daemon thread so it never blocks the Qt event loop.

FIX CHANGELOG
─────────────
BUG — cwd was captured AFTER the panel stole focus, so xdotool reported
      the panel window instead of the user's actual working app.

FIX — _fire() now calls WindowDetector._get_<os>_info() and
      _get_window_cwd() synchronously (both are blocking OS calls, safe
      to run in the pynput daemon thread) and writes the result into
      PanelState.pre_panel_context *before* emitting panel_toggle_requested.
      input_adapter reads that field and injects it into every
      "text_query_received" payload, so the orchestrator always receives the
      correct cwd regardless of which window is active when the user submits.
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
                log.warning(
                    "hotkey_listener: unknown key '%s' in hotkey '%s'", part, hotkey_str
                )
        else:
            keys.add(_kb.KeyCode.from_char(part))
    return keys


class HotkeyListener:
    """
    Listens for a configurable global hotkey and fires an EventBus event.

    Args:
        hotkey_str:      pynput-style string, e.g. '<ctrl>+<space>'
        event_bus:       EventBus instance (must have .publish())
        window_detector: WindowDetector instance — used to snapshot the
                         active window *before* the panel is shown.
        panel_state:     PanelState instance — pre_panel_context is written
                         here so input_adapter can read it later.
    """

    def __init__(
        self,
        hotkey_str: str,
        event_bus: Any,
        window_detector: Any,
        panel_state: Any,
    ) -> None:
        self._hotkey_str = hotkey_str
        self._bus = event_bus
        self._window_detector = window_detector
        self._panel_state = panel_state
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

    def _snapshot_current_context(self) -> dict | None:
        """
        Synchronously read the active window title + pid + cwd using the
        same OS methods that WindowDetector uses.

        This runs in the pynput daemon thread — both _get_<os>_info() and
        _get_window_cwd() are ordinary blocking calls, which is fine here.

        Returns a minimal context dict or None if the detector is unavailable.
        """
        wd = self._window_detector
        if wd is None:
            return None

        try:
            os_name = wd.os_name

            if os_name == "Linux":
                title, pid = wd._get_linux_info()
            elif os_name == "Windows":
                title, pid = wd._get_windows_info()
            elif os_name == "Darwin":
                title, pid = wd._get_macos_info()
            else:
                return None

            # If somehow our own window is already focused, fall back to
            # the last known external snapshot rather than recording ourselves.
            if wd._is_own_window(title):
                return wd._last_external_snapshot

            cwd = wd._get_window_cwd(pid)

            return {
                "window_title": title,
                "window_pid":   pid,
                "cwd":          cwd,
            }

        except Exception as exc:
            log.warning("hotkey_listener: context snapshot failed — %s", exc)
            # Fall back to whatever the detector last saw externally.
            return getattr(wd, "_last_external_snapshot", None)

    def _fire(self) -> None:
        # ── Step 1: capture context NOW, before the panel window appears ──
        context = self._snapshot_current_context()
        if context:
            self._panel_state.pre_panel_context = context
            log.debug(
                "hotkey_listener: pre-panel context saved — cwd=%s window='%s'",
                context.get("cwd"),
                context.get("window_title"),
            )
        else:
            log.debug("hotkey_listener: no pre-panel context available.")

        # ── Step 2: ask the panel to toggle ───────────────────────────────
        try:
            self._bus.publish("panel_toggle_requested", {"source": "hotkey"})
        except Exception as exc:  # noqa: BLE001
            log.warning("hotkey_listener: could not publish event — %s", exc)