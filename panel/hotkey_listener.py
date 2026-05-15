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

        XDOTOOL FALLBACK FIX:
        On this system, xdotool's _NET_ACTIVE_WINDOW query fails when the
        Operonix panel or a Qt widget holds X11 focus.  _get_linux_info()
        returns ('Unknown Linux Window', None) in that case.

        When that happens we fall back to wmctrl Z-order: the topmost
        non-Operonix window in `wmctrl -l -p` is the window the user was
        working in before the hotkey fired.  We read its cwd directly from
        /proc/<pid>/cwd — no xdotool needed.

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

            # ── xdotool failure fallback ───────────────────────────────────
            # _get_linux_info() returns 'Unknown Linux Window' / None when
            # xdotool's _NET_ACTIVE_WINDOW is unavailable (common when a Qt
            # panel or Wayland surface holds focus).
            # Fall back to wmctrl Z-order which does NOT depend on xdotool.
            if not title or title == "Unknown Linux Window" or pid is None:
                log.debug(
                    "hotkey_listener: xdotool returned Unknown — trying wmctrl fallback"
                )
                wmctrl_ctx = self._snapshot_via_wmctrl()
                if wmctrl_ctx:
                    self._panel_state.pre_panel_context = wmctrl_ctx
                    log.debug(
                        "hotkey_listener: wmctrl fallback succeeded — cwd=%s window='%s'",
                        wmctrl_ctx.get("cwd"),
                        wmctrl_ctx.get("window_title"),
                    )
                    return wmctrl_ctx

                # wmctrl also failed — use last known external snapshot
                snap = getattr(wd, "_last_external_snapshot", None)
                if snap:
                    log.debug(
                        "hotkey_listener: using last_external_snapshot cwd=%s",
                        snap.get("cwd"),
                    )
                return snap

            # If our own window is already focused (panel clicked before
            # hotkey — edge case), fall back to last known external snapshot.
            if wd._is_own_window(title):
                snap = wd._last_external_snapshot
                log.debug(
                    "hotkey_listener: own-window at fire time — using last external: %s",
                    snap,
                )
                return snap

            # Classify the window so _get_window_cwd uses the right strategy.
            try:
                from context.app_classifier import classifier as _clf
                app_ctx = _clf.classify(title)
                app_type = app_ctx.category
            except Exception:
                app_type = "unknown"

            if app_type == "unknown":
                app_type = wd._infer_file_manager(pid, title)

            cwd = wd._get_window_cwd(pid, app_type, title)

            return {
                "window_title": title,
                "window_pid":   pid,
                "app_type":     app_type,
                "cwd":          cwd,
            }

        except Exception as exc:
            log.warning("hotkey_listener: context snapshot failed — %s", exc)
            return getattr(wd, "_last_external_snapshot", None)

    def _snapshot_via_wmctrl(self) -> dict | None:
        """
        Fallback context snapshot using wmctrl Z-order.

        Reads `wmctrl -l -p`, finds the topmost window that:
          - Is not owned by our own PIDs (Operonix process tree)
          - Has a real pid (pid > 1)

        Then reads cwd from /proc/<pid>/cwd, walking children if the
        direct cwd is / (gnome-terminal-server pattern).

        Returns a context dict or None.
        """
        import shutil
        import subprocess
        import os

        if not shutil.which("wmctrl"):
            log.debug("_snapshot_via_wmctrl: wmctrl not available")
            return None

        # Collect our own pids to exclude Operonix windows
        own_pids: set[int] = set()
        wd = self._window_detector
        if wd is not None:
            own_pids = getattr(wd, "_own_pids", set()) or set()
            # Also add the terminal_resolver blacklist if available
            try:
                from core.terminal_resolver import terminal_resolver as _tr
                own_pids |= getattr(_tr, "_own_pids", set())
            except Exception:
                pass

        # Also always exclude our own process PID and its ancestors
        own_pids.add(os.getpid())

        try:
            out = subprocess.check_output(
                ["wmctrl", "-l", "-p"],
                text=True, timeout=2.0,
                stderr=subprocess.DEVNULL,
            )
        except Exception as exc:
            log.debug("_snapshot_via_wmctrl: wmctrl failed — %s", exc)
            return None

        for line in out.strip().splitlines():
            parts = line.split(None, 4)
            if len(parts) < 4:
                continue
            try:
                pid = int(parts[2])
            except ValueError:
                continue
            if pid <= 1 or pid in own_pids:
                continue

            title = parts[4] if len(parts) > 4 else ""

            # Skip clearly non-useful windows
            skip_titles = ("Unknown", "Desktop", "")
            if any(title.strip().startswith(s) for s in skip_titles):
                continue

            # Read cwd — walk children if direct cwd is / (terminal server pattern)
            cwd = self._read_cwd_for_pid(pid)

            # Classify app type from title
            try:
                from context.app_classifier import classifier as _clf
                app_ctx  = _clf.classify(title)
                app_type = app_ctx.category
            except Exception:
                app_type = "unknown"

            log.debug(
                "_snapshot_via_wmctrl: picked window pid=%d title=%r cwd=%s app_type=%s",
                pid, title[:60], cwd, app_type,
            )

            return {
                "window_title": title,
                "window_pid":   pid,
                "app_type":     app_type,
                "cwd":          cwd,
            }

        log.debug("_snapshot_via_wmctrl: no suitable window found in wmctrl output")
        return None

    @staticmethod
    def _read_cwd_for_pid(pid: int) -> str | None:
        """
        Read /proc/<pid>/cwd.  If the cwd is / or another system root
        (typical for gnome-terminal-server), walk one level of children
        via /proc/<pid>/task/<tid>/children to find the shell's real cwd.
        """
        import os

        def _cwd(p: int) -> str | None:
            try:
                c = os.readlink(f"/proc/{p}/cwd")
                if c and c not in ("/", "/usr", "/usr/bin"):
                    return c
            except OSError:
                pass
            return None

        result = _cwd(pid)
        if result:
            return result

        # Walk children
        task_dir = f"/proc/{pid}/task"
        try:
            for tid in os.listdir(task_dir):
                try:
                    with open(f"{task_dir}/{tid}/children") as f:
                        for cpid_str in f.read().split():
                            try:
                                c = _cwd(int(cpid_str))
                                if c:
                                    return c
                            except ValueError:
                                pass
                except OSError:
                    pass
        except OSError:
            pass

        # Return raw cwd even if it's / — better than None
        try:
            return os.readlink(f"/proc/{pid}/cwd")
        except OSError:
            return None

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