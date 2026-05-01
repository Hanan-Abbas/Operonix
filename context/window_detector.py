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


# Number of consecutive background polls that must return the same title
# before that title is committed as the stable context snapshot.
# This prevents a window the user glanced at briefly (while switching to
# the panel) from overwriting the real working context.
_TITLE_STABLE_POLLS = 2


class WindowDetector:
    def __init__(self) -> None:
        self.os_name = platform.system()
        self.ewmh    = None
        self.win32gui = None
        self.last_title: str | None = None
        self._last_external_snapshot: dict | None = None

        # Panel state tracking
        # _panel_open:          True while the Operonix panel has OS focus.
        # _pre_panel_snapshot:  snapshot captured just before the panel stole
        #                       focus — the app the user actually wants to act on.
        # _title_seen_count:    consecutive poll count per title; we only commit
        #                       a new snapshot after _TITLE_STABLE_POLLS matches
        #                       to avoid acting on a transient/glanced window.
        self._panel_open: bool = False
        self._pre_panel_snapshot: dict | None = None
        self._title_seen_count: dict[str, int] = {}
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
        # Track panel open/close so we freeze the snapshot while the panel
        # has focus and don't let background polls overwrite the real context.
        bus.subscribe("panel_opened", self._on_panel_opened)
        bus.subscribe("panel_closed", self._on_panel_closed)
        bus.subscribe("panel_hidden", self._on_panel_closed)
        logger.info("Window Detector: Active on %s", self.os_name)
        await asyncio.sleep(1)
        await self.capture_snapshot(
            type("Event", (), {"data": {"task_id": "initial_boot"}})()
        )
        asyncio.create_task(self._poll_loop())

    async def _on_panel_opened(self, event: object) -> None:
        # Fired the moment the Operonix panel gains focus.
        # Capture the CURRENT foreground window before focus moves to the panel.
        # This is the app the user actually wants to act on.
        self._panel_open = True
        await self._capture_real_snapshot(task_id="pre_panel")
        if self._last_external_snapshot:
            self._pre_panel_snapshot = dict(self._last_external_snapshot)
            logger.debug(
                "Panel opened — locked pre-panel context: app=%s cwd=%s",
                self._pre_panel_snapshot.get("app_name"),
                self._pre_panel_snapshot.get("cwd"),
            )

    async def _on_panel_closed(self, event: object) -> None:
        # Panel closed/hidden — resume normal background polling.
        self._panel_open = False
        self._pre_panel_snapshot = None
        logger.debug("Panel closed — resuming normal context tracking.")

    async def _poll_loop(self) -> None:
        while True:
            # While the panel is open, skip polls — the panel window title
            # would overwrite the real context we locked in _on_panel_opened.
            if not self._panel_open:
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

    def _get_window_cwd(
        self,
        pid: int | None,
        app_type: str = "",
        window_title: str = "",
    ) -> str:
        """
        Resolve the TRUE working directory for the focused window.

        This is app-type-aware.  Reading /proc/<pid>/cwd for a file manager
        gives the manager's install dir, NOT the folder the user is browsing.
        Each app type needs a different strategy:

          file_manager  → parse window title / query D-Bus / AppleScript
          terminal      → read cwd of the shell child process
          browser       → skip (URL ≠ filesystem path); return os.getcwd()
          code_editor   → /proc/<pid>/cwd  (editor sets cwd to project root)
          everything else → /proc/<pid>/cwd or psutil fallback
        """
        if not pid:
            return os.getcwd()

        try:
            if app_type == "file_manager":
                cwd = self._cwd_from_file_manager(pid, window_title)
                if cwd:
                    return cwd
                # fall through to process cwd as last resort

            elif app_type == "terminal":
                cwd = self._cwd_from_terminal_child(pid)
                if cwd:
                    return cwd
                # fall through

            elif app_type == "browser":
                return os.getcwd()

            # Default: process's own cwd
            if self.os_name == "Linux":
                cwd_link = Path(f"/proc/{pid}/cwd")
                if cwd_link.exists():
                    return str(cwd_link.resolve())
            else:
                import psutil
                return psutil.Process(pid).cwd()

        except Exception as exc:
            logger.debug(
                "CWD resolution failed pid=%s app_type=%s: %s", pid, app_type, exc
            )

        return os.getcwd()

    def _cwd_from_file_manager(self, pid: int, window_title: str) -> str | None:
        """
        Resolve the folder currently displayed in a file manager.

        Linux strategies (tried in order):
          1. Nautilus D-Bus — exact location, no parsing needed
          2. Window title starts with '/' — it is the path (Thunar, Nemo)
          3. Match title word against XDG standard dirs and a bounded find

        Windows: SHGetFolderPath
        macOS:   AppleScript Finder target
        """
        if self.os_name == "Linux":
            # Strategy 1: Nautilus D-Bus
            try:
                gio = subprocess.run(
                    [
                        "gdbus", "call", "--session",
                        "--dest", "org.gnome.Nautilus",
                        "--object-path", "/org/gnome/Nautilus/window/1",
                        "--method", "org.freedesktop.DBus.Properties.Get",
                        "org.gnome.Nautilus.Window", "Location",
                    ],
                    capture_output=True, text=True, timeout=1,
                )
                if gio.returncode == 0 and "file://" in gio.stdout:
                    import urllib.parse
                    raw = gio.stdout.split("file://")[1].split("'")[0]
                    decoded = urllib.parse.unquote(raw).strip()
                    if decoded and Path(decoded).is_dir():
                        return decoded
            except Exception:
                pass

            # Strategy 2: title is a full path (Thunar / Nemo / Dolphin)
            candidate = window_title.strip().split(" ")[0]
            if candidate.startswith("/") and Path(candidate).is_dir():
                return candidate

            # Strategy 3: match title word against known dirs + bounded find
            title_word = (
                window_title.strip()
                .split("—")[0].split("–")[0].split("-")[0]
                .strip()
            )
            if title_word:
                return self._search_folder_by_name(title_word)

        elif self.os_name == "Windows":
            try:
                import ctypes
                buf = ctypes.create_unicode_buffer(260)
                ctypes.windll.shell32.SHGetFolderPathW(0, 0, 0, 0, buf)
                return buf.value or None
            except Exception:
                pass

        elif self.os_name == "Darwin":
            try:
                script = (
                    'tell application "Finder" to get POSIX path '
                    'of (target of front window as alias)'
                )
                result = subprocess.run(
                    ["osascript", "-e", script],
                    capture_output=True, text=True, timeout=2,
                )
                if result.returncode == 0:
                    path = result.stdout.strip()
                    if path and Path(path).is_dir():
                        return path
            except Exception:
                pass

        return None

    def _search_folder_by_name(self, name: str) -> str | None:
        """
        Given a display name (e.g. 'Screenshots'), find its real path by
        checking XDG standard locations first, then a bounded find.
        """
        if not name or len(name) < 2:
            return None

        home = Path.home()
        priority_parents = [
            home,
            home / "Pictures",
            home / "Documents",
            home / "Downloads",
            home / "Desktop",
            home / "Videos",
            home / "Music",
            Path("/tmp"),
        ]
        for parent in priority_parents:
            candidate = parent / name
            if candidate.is_dir():
                return str(candidate)

        # Broader bounded search — depth 4 stays fast (<3 s on most systems)
        try:
            result = subprocess.run(
                ["find", str(home), "-maxdepth", "4", "-type", "d", "-name", name],
                capture_output=True, text=True, timeout=3,
            )
            hits = [h for h in result.stdout.strip().splitlines() if h]
            if hits:
                return hits[0]
        except Exception:
            pass

        return None

    def _cwd_from_terminal_child(self, pid: int) -> str | None:
        """
        Terminal emulators spawn a shell as a child.  The shell's cwd is
        what matters, not the terminal's own cwd.
        """
        try:
            if self.os_name == "Linux":
                result = subprocess.run(
                    ["pgrep", "--parent", str(pid)],
                    capture_output=True, text=True, timeout=1,
                )
                children = [c for c in result.stdout.strip().splitlines() if c.isdigit()]
                if children:
                    cwd_link = Path(f"/proc/{children[-1]}/cwd")
                    if cwd_link.exists():
                        return str(cwd_link.resolve())
            else:
                import psutil
                kids = psutil.Process(pid).children(recursive=False)
                if kids:
                    return kids[-1].cwd()
        except Exception:
            pass
        return None

    # ── Snapshot capture ───────────────────────────────────────────────────

    async def _get_current_title_and_pid(self) -> tuple[str, int | None]:
        """OS-agnostic helper — returns (title, pid) for the active window."""
        if self.os_name == "Linux":
            return self._get_linux_info()
        elif self.os_name == "Windows" and self.win32gui:
            return self._get_windows_info()
        elif self.os_name == "Darwin":
            return self._get_macos_info()
        return "Unknown", None

    async def _capture_real_snapshot(self, task_id: str = "internal") -> None:
        """Build and cache a snapshot for the current foreground window.
        Does NOT emit an event — only updates _last_external_snapshot.
        Called by _on_panel_opened to lock the pre-panel context.
        """
        try:
            current_title, pid = await self._get_current_title_and_pid()
            if self._is_own_window(current_title):
                return  # panel is already on top, nothing real to capture
            app_context = await classifier.classify_async(current_title)
            cwd = await asyncio.get_running_loop().run_in_executor(
                None, self._get_window_cwd, pid,
                app_context.category, current_title,
            )
            self._last_external_snapshot = {
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
            self.last_title = current_title
            logger.debug(
                "_capture_real_snapshot: window='%s' cwd='%s'", current_title, cwd
            )
        except Exception as exc:
            logger.warning("_capture_real_snapshot error: %s", exc)

    async def capture_snapshot(self, event: object) -> None:
        data_payload = getattr(event, "data", {})
        task_id      = data_payload.get("task_id", "background_poll")

        try:
            current_title, pid = await self._get_current_title_and_pid()

            # ── Own-window guard ──────────────────────────────────────────
            # The panel is in the foreground.  Serve the pre-panel snapshot
            # (captured the moment the panel opened) so the task always
            # operates on the app the user was actually looking at.
            if self._is_own_window(current_title):
                if task_id == "background_poll":
                    # Background polls with the panel open are a no-op;
                    # _poll_loop already skips them but guard here too.
                    return

                # Priority order for real task snapshots:
                #   1. _pre_panel_snapshot  — captured at panel-open time
                #   2. _last_external_snapshot — last good poll result
                #   3. pre_panel_context injected by input_adapter
                cached = (
                    self._pre_panel_snapshot
                    or self._last_external_snapshot
                    or data_payload.get("pre_panel_context")
                )
                if cached is not None:
                    reply = dict(cached)
                    reply["task_id"] = task_id
                    await bus.emit("context_snapshot_ready", reply, source="window_detector")
                    logger.debug(
                        "Own-window guard: served pre-panel context app=%s cwd=%s",
                        reply.get("app_name"), reply.get("cwd"),
                    )
                else:
                    logger.warning(
                        "Own-window guard: no cached context for task %s", task_id
                    )
                return

            # ── Stability threshold ───────────────────────────────────────
            # Only commit a new snapshot after the same title has appeared
            # _TITLE_STABLE_POLLS times in a row.  This prevents a window
            # the user merely glanced at (e.g. VS Code flashing on screen
            # while switching to the file manager) from overwriting the
            # real working context.
            if task_id == "background_poll":
                if current_title != self.last_title:
                    # Title changed — start counting stability
                    self._title_seen_count = {current_title: 1}
                    return  # wait for next poll to confirm
                else:
                    count = self._title_seen_count.get(current_title, 0) + 1
                    self._title_seen_count[current_title] = count
                    if count < _TITLE_STABLE_POLLS:
                        return  # not stable yet
                    # Stable — fall through to build and commit the snapshot

            self.last_title = current_title

            # Classify and resolve cwd
            app_context = await classifier.classify_async(current_title)
            cwd = await asyncio.get_running_loop().run_in_executor(
                None, self._get_window_cwd, pid,
                app_context.category, current_title,
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
            self._title_seen_count = {current_title: _TITLE_STABLE_POLLS}
            await bus.emit("context_snapshot_ready", snapshot, source="window_detector")
            logger.debug(
                "Snapshot committed: window='%s' cwd='%s' pid=%s",
                current_title, cwd, pid,
            )

        except Exception as exc:
            if task_id != "background_poll":
                logger.error("WindowDetector error: %s", exc)


window_detector = WindowDetector()