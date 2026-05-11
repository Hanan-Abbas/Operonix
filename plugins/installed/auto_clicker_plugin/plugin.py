from __future__ import annotations
import sys as _sys, os as _os
_plugin_dir = _os.path.abspath(_os.path.dirname(__file__))
_project_root = _os.path.dirname(
    _os.path.dirname(_os.path.dirname(_plugin_dir)))
if _project_root not in _sys.path:
    _sys.path.insert(0, _project_root)

"""
Plugin: auto_clicker_plugin
Intent: auto clicker
Category: background
Description: Auto-generated plugin to handle: auto clicker
Version: 1.0
Generated: 2026-05-10 09:29 UTC
"""

# Standard library imports — always available
import time
import threading
import os
import sys

# NOTE: from __future__ imports and sys.path bootstrap are injected
# automatically by sandbox_runner — do NOT add them here.

from plugins.manifest_schema import BasePlugin

class AutoClickerPlugin(BasePlugin):
    """
    Auto-generated plugin to handle: auto clicker
    Category: background daemon (infinite loop with stop trigger)

    Pattern: starts a daemon thread that loops until a stop event fires.
    The run() method starts the threads and returns immediately.
    """
    name             = "auto_clicker_plugin"
    description      = "Auto-generated plugin to handle: auto clicker"
    version          = "1.0"
    permissions      = ["ui_interaction"]
    safe_mode        = True
    allowed_services = []

    # Class-level stop event — shared across calls
    _stop_event: threading.Event | None = None

    def validate(self, args: dict) -> str | None:
        # No required args for background tasks — all config has sensible defaults
        return None

    async def run(self, context: dict, args: dict) -> dict:
        try:
            import pyautogui
            import threading

            # ── Configuration from args (with safe defaults) ─────────────────
            interval   = float(args.get("interval", 0.1))   # seconds between actions
            stop_hotkey = str(args.get("stop_hotkey", "ctrl+a+s"))

            # ── Stop any previous instance first ──────────────────────────────
            if AutoClickerPlugin._stop_event is not None:
                AutoClickerPlugin._stop_event.set()
                time.sleep(0.2)

            stop_event = threading.Event()
            AutoClickerPlugin._stop_event = stop_event

            # ── Worker: performs the repeating action ─────────────────────────
            def _worker(stop: threading.Event, iv: float) -> None:
                pyautogui.FAILSAFE = True
                while not stop.is_set():
                    # Replace this line with the actual repeating action
                    pyautogui.click()
                    stop.wait(iv)  # waits iv seconds OR until stop is set

            # ── Stopper: listens for hotkey using pynput (no root required) ──────
            def _stopper(stop: threading.Event, hotkey_str: str) -> None:
                try:
                    from pynput import keyboard as _kb
                    parts    = [p.strip().lower() for p in hotkey_str.split("+")]
                    _MOD_MAP = {
                        "alt": _kb.Key.alt, "ctrl": _kb.Key.ctrl,
                        "shift": _kb.Key.shift, "cmd": _kb.Key.cmd,
                    }
                    modifiers = {_MOD_MAP[p] for p in parts[:-1] if p in _MOD_MAP}
                    char_key  = parts[-1]
                    pressed   = set()

                    def on_press(key):
                        pressed.add(key)
                        try:    k = key.char
                        except: k = None
                        if all(m in pressed for m in modifiers) and k == char_key:
                            stop.set()
                            return False  # stop listener

                    def on_release(key):
                        pressed.discard(key)

                    with _kb.Listener(on_press=on_press, on_release=on_release) as lst:
                        while not stop.is_set():
                            time.sleep(0.05)
                        lst.stop()
                except ImportError:
                    # pynput not installed — fall back to 60s auto-stop
                    stop.wait(60)
                    stop.set()
                except Exception as _e:
                    stop.set()

            threading.Thread(
                target=_worker, args=(stop_event, interval), daemon=True
            ).start()
            threading.Thread(
                target=_stopper, args=(stop_event, stop_hotkey), daemon=True
            ).start()

            return {
                "status":    "success",
                "result":    "started",
                "intent":    "auto clicker",
                "stop_with": stop_hotkey,
                "interval":  interval,
            }

        except Exception as e:
            return {"status": "error", "message": str(e), "intent": "auto clicker"}