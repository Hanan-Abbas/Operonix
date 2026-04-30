"""
context/window_detector.py
───────────────────────────

FIX CHANGELOG (this revision)
──────────────────────────────
BUG 1 — snapshot never contained a "cwd" field.
    The planner and executor need cwd to resolve location hints like
    "here" or "current window" into real filesystem paths.  Without it,
    os.getcwd() (the Operonix process directory) was used as fallback,
    which is wrong — the user wants the directory of their active app.

    FIX: Added _get_window_cwd() which resolves the CWD of the focused
    window's process using OS-native methods:
      Linux   — /proc/<pid>/cwd  symlink (zero dependencies)
      Windows — psutil.Process(pid).cwd()  (psutil already in requirements)
      macOS   — psutil.Process(pid).cwd()

    PID is obtained from the same OS APIs already used for the title.
    If CWD resolution fails for any reason, falls back to os.getcwd()
    so nothing downstream breaks.

    The "cwd" key is now always present in the snapshot dict.

BUG 2 — own-window guard silently dropped the snapshot when
         _last_external_snapshot was None (e.g. first launch, or the
         background poll hadn't run yet before the user triggered the hotkey).

    FIX: capture_snapshot() now also accepts an optional "pre_panel_context"
    key in the event payload (written there by input_adapter, sourced from
    HotkeyListener). When the own-window guard fires and
    _last_external_snapshot is None, we use pre_panel_context as the
    fallback so the orchestrator always gets a valid cwd.
"""
from __future__ import annotations

import asyncio
import logging
import os
import platform
import subprocess
from pathlib import Path

from core.event_bus import bus
from context.app_classifier import classifier

logger = logging.getLogger("WindowDetector")

_OWN_WINDOW_SUBSTRINGS = [
    "operonix",
    "panel",
    "command panel",
]


class WindowDetector:
    def __init__(self) -> None:
        self.os_name = platform.system()
        self.ewmh    = None
        self.win32gui = None
        self.last_title: str | None = None
        self._last_external_snapshot: dict | None = None
        self._setup_os_imports()

    # ── OS import setup ────────────────────────────────────────────────────

    def _setup_os_imports(self) -> None:
        try:
            if self.os_name == "Windows":
                import win32gui
                self.win32gui = win32gui
            elif self.os_name == "Linux":
                try:
                    from ewmh import EWMH
                    self.ewmh = EWMH()
                except ImportError:
                    pass
            elif self.os_name == "Darwin":
                try:
                    from AppKit import NSWorkspace
                    from Quartz import CGWindowListCopyWindowInfo
                    self.NSWorkspace = NSWorkspace
                    self.CGWindowListCopyWindowInfo = CGWindowListCopyWindowInfo
                except ImportError:
                    logger.warning("WindowDetector: Mac libraries (pyobjc) missing.")
        except Exception as exc:
            logger.warning("WindowDetector setup error: %s", exc)

    def _is_own_window(self, title: str) -> bool:
        t = (title or "").lower()
        return any(sub in t for sub in _OWN_WINDOW_SUBSTRINGS)

    # ── Lifecycle ──────────────────────────────────────────────────────────

    async def start(self) -> None:
        bus.subscribe("request_context_snapshot", self.capture_snapshot)
        logger.info("Window Detector: Active on %s", self.os_name)
        await asyncio.sleep(1)
        await self.capture_snapshot(
            type("Event", (), {"data": {"task_id": "initial_boot"}})()
        )
        asyncio.create_task(self._poll_loop())

    async def _poll_loop(self) -> None:
        while True:
            await self.capture_snapshot(
                type("Event", (), {"data": {"task_id": "background_poll"}})()
            )
            await asyncio.sleep(2)

    # ── OS-specific title + PID fetchers ──────────────────────────────────

    def _get_linux_info(self) -> tuple[str, int | None]:
        """Returns (window_title, pid)."""
        title = "Unknown Linux Window"
        pid: int | None = None
        try:
            title = subprocess.check_output(
                ["xdotool", "getactivewindow", "getwindowname"],
                stderr=subprocess.STDOUT,
            ).decode().strip()
            pid_str = subprocess.check_output(
                ["xdotool", "getactivewindow", "getwindowpid"],
                stderr=subprocess.STDOUT,
            ).decode().strip()
            pid = int(pid_str) if pid_str.isdigit() else None
        except Exception:
            try:
                if self.ewmh:
                    win = self.ewmh.getActiveWindow()
                    if win:
                        name = (
                            self.ewmh.get_wm_name(win)
                            if hasattr(self.ewmh, "get_wm_name")
                            else self.ewmh.getWMName(win)
                        )
                        title = name.decode("utf-8") if isinstance(name, bytes) else name
                        try:
                            pid = self.ewmh._getProperty("_NET_WM_PID", win)
                        except Exception:
                            pass
            except Exception:
                pass
        return title, pid

    def _get_windows_info(self) -> tuple[str, int | None]:
        """Returns (window_title, pid)."""
        try:
            import ctypes
            hwnd  = self.win32gui.GetForegroundWindow()
            title = self.win32gui.GetWindowText(hwnd)
            pid   = ctypes.c_ulong()
            ctypes.windll.user32.GetWindowThreadProcessId(hwnd, ctypes.byref(pid))
            return title, pid.value or None
        except Exception:
            return "Unknown Windows Window", None

    def _get_macos_info(self) -> tuple[str, int | None]:
        """Returns (window_title, pid)."""
        try:
            curr_app = self.NSWorkspace.sharedWorkspace().frontmostApplication()
            pid      = curr_app.processIdentifier()
            window_list = self.CGWindowListCopyWindowInfo(1 << 0, 0)
            for window in window_list:
                if window["kCGWindowOwnerPID"] == pid:
                    return window.get("kCGWindowName", curr_app.localizedName()), pid
            return curr_app.localizedName(), pid
        except Exception:
            return "Unknown Mac Window", None

    # ── CWD resolution ─────────────────────────────────────────────────────

    def _get_window_cwd(self, pid: int | None) -> str:
        """
        Resolve the working directory of the process that owns the focused
        window.  Falls back to os.getcwd() if resolution fails.

        Linux:   /proc/<pid>/cwd symlink — no extra dependencies.
        Windows: psutil.Process(pid).cwd()
        macOS:   psutil.Process(pid).cwd()
        """
        if not pid:
            return os.getcwd()

        try:
            if self.os_name == "Linux":
                cwd_link = Path(f"/proc/{pid}/cwd")
                if cwd_link.exists():
                    return str(cwd_link.resolve())

            else:  # Windows and macOS
                import psutil
                return psutil.Process(pid).cwd()

        except Exception as exc:
            logger.debug("CWD resolution failed for pid=%s: %s", pid, exc)

        return os.getcwd()

    # ── Snapshot capture ───────────────────────────────────────────────────

    async def capture_snapshot(self, event: object) -> None:
        data_payload  = getattr(event, "data", {})
        task_id       = data_payload.get("task_id", "background_poll")
        current_title = "Unknown"
        pid: int | None = None

        try:
            if self.os_name == "Linux":
                current_title, pid = self._get_linux_info()
            elif self.os_name == "Windows" and self.win32gui:
                current_title, pid = self._get_windows_info()
            elif self.os_name == "Darwin":
                current_title, pid = self._get_macos_info()

            # Own-window guard — the panel is active, serve cached context.
            if self._is_own_window(current_title):
                if task_id == "background_poll":
                    # Background polls while panel is open are intentionally
                    # silent — we don't want to overwrite the last real context.
                    return

                # For real task snapshots: prefer the last known external
                # snapshot. If that is not available yet (first launch edge
                # case), fall back to pre_panel_context that was injected
                # into the event payload by input_adapter.
                cached = self._last_external_snapshot
                if cached is None:
                    cached = data_payload.get("pre_panel_context")

                if cached is not None:
                    reply = dict(cached)
                    reply["task_id"] = task_id
                    await bus.emit("context_snapshot_ready", reply, source="window_detector")
                    logger.debug(
                        "Own-window guard: served cached context cwd=%s for task %s",
                        reply.get("cwd"), task_id,
                    )
                else:
                    logger.warning(
                        "Own-window guard: no cached context available for task %s", task_id
                    )
                return

            # Skip unchanged titles during background polls
            if current_title == self.last_title and task_id == "background_poll":
                return

            self.last_title = current_title

            # Classify window
            app_context = await classifier.classify_async(current_title)

            # Resolve CWD of the focused window's process
            cwd = await asyncio.get_running_loop().run_in_executor(
                None, self._get_window_cwd, pid
            )

            snapshot = {
                "window_title": current_title,
                "app_name":    app_context.app_name,
                "app_type":    app_context.category,
                "sub_context": app_context.sub_context,
                "confidence":  app_context.confidence,
                "llm_used":    app_context.llm_used,
                "app_context": app_context.to_dict(),
                "cwd":         cwd,
                "window_pid":  pid,
                "task_id":     task_id,
            }

            self._last_external_snapshot = snapshot
            await bus.emit("context_snapshot_ready", snapshot, source="window_detector")

            logger.debug(
                "Snapshot: window='%s' cwd='%s' pid=%s",
                current_title, cwd, pid,
            )

        except Exception as exc:
            if task_id != "background_poll":
                logger.error("WindowDetector error: %s", exc)


window_detector = WindowDetector()