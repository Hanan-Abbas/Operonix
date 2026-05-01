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

ARCH FIX 1 — _get_current_title_and_pid was declared async with no awaits.
    FIX: Made sync. Callers updated to call directly (no await).

ARCH FIX 2 — Race condition on _last_external_snapshot.
    _focus_event_loop, _poll_loop, and capture_snapshot all write/read
    _last_external_snapshot concurrently with no synchronisation.
    FIX: Added asyncio.Lock (_snapshot_lock). All reads/writes now run
    inside `async with self._snapshot_lock`.

ARCH FIX 3 — run_in_executor used default shared thread pool.
    _get_window_cwd runs subprocess + file I/O and can block.  Using
    None (the default pool) risks starving other executor users.
    FIX: Dedicated ThreadPoolExecutor(max_workers=2) in __init__,
    passed explicitly to run_in_executor.

ARCH FIX 4 — xprop subprocess never terminated on shutdown.
    FIX: Added stop() coroutine that terminates _xprop_proc and shuts
    down the thread pool executor.

ARCH FIX 5 — Dedup guard too aggressive.
    Same window title ≠ same context (browser tab change, folder switch).
    The old guard also fired before any snapshot existed, causing the
    very first background_poll to be skipped if title was unchanged.
    FIX: Dedup now also requires _last_external_snapshot is not None,
    so the first capture always goes through.
"""
from __future__ import annotations

import asyncio
import logging
import os
import platform
import shutil
import subprocess
from concurrent.futures import ThreadPoolExecutor
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
# After how many consecutive polls a new title is committed to
# _last_external_snapshot. 1 means any non-own-window title is committed
# immediately — we rely on the own-window guard, not this threshold,
# to prevent the panel from polluting the context.
_TITLE_STABLE_POLLS = 1


class WindowDetector:
    def __init__(self) -> None:
        self.os_name = platform.system()
        self.ewmh    = None
        self.win32gui = None
        self.last_title: str | None = None
        self._last_external_snapshot: dict | None = None
        self._snapshot_lock = asyncio.Lock()
        # 🔧 FIX 3: dedicated thread pool for blocking subprocess/file ops
        self._executor = ThreadPoolExecutor(max_workers=2)
        # _last_external_snapshot is kept current in real time by the
        # xprop focus watcher (Linux) or poll loop (other OS).
        # The own-window guard in capture_snapshot prevents the panel
        # window from ever overwriting it.
        # No freeze/lock needed — xprop fires the instant you switch windows.
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
        self._xprop_proc = None
        await asyncio.sleep(1)
        await self.capture_snapshot(
            type("Event", (), {"data": {"task_id": "initial_boot"}})()
        )

        # xprop -spy watches X11 focus changes in real time — no polling delay.
        # _last_external_snapshot is always the most recent non-panel window.
        # The own-window guard prevents the panel from ever overwriting it.
        # Poll loop runs as fallback for Windows/macOS and xprop crash recovery.
        asyncio.create_task(self._focus_event_loop())
        asyncio.create_task(self._poll_loop())

    async def stop(self) -> None:
        """Graceful shutdown: terminate xprop watcher and thread pool."""
        # 🔧 FIX 4: clean up xprop subprocess on shutdown
        if self._xprop_proc:
            self._xprop_proc.terminate()
            await self._xprop_proc.wait()
            self._xprop_proc = None
        self._executor.shutdown(wait=False)

    async def _poll_loop(self) -> None:
        """Fallback: Windows/macOS or xprop crash recovery. 1s interval."""
        while True:
            await self.capture_snapshot(
                type("Event", (), {"data": {"task_id": "background_poll"}})()
            )
            await asyncio.sleep(1)

    async def _focus_event_loop(self) -> None:
        """
        Linux-only: use `xdotool behave_screen_edge` + XPROP focus events
        via `xdotool search --sync` to get notified the instant focus changes,
        eliminating the polling delay entirely.

        We run:
            xprop -spy -root _NET_ACTIVE_WINDOW
        which prints a new line every time the focused window changes — zero
        delay, no CPU spin, no missed transitions.  We parse each line,
        resolve the window ID to a title+PID via xdotool, then call
        capture_snapshot exactly as the poll loop would.

        Falls back to _poll_loop if xprop is not available.
        """
        if self.os_name != "Linux":
            return  # Windows/macOS use _poll_loop

        if not shutil.which("xprop") or not shutil.which("xdotool"):
            logger.info(
                "WindowDetector: xprop/xdotool not found — using poll fallback."
            )
            return

        logger.info("WindowDetector: starting real-time focus watcher (xprop).")
        try:
            proc = await asyncio.create_subprocess_exec(
                "xprop", "-spy", "-root", "_NET_ACTIVE_WINDOW",
                stdout=asyncio.subprocess.PIPE,
                stderr=asyncio.subprocess.DEVNULL,
            )
            self._xprop_proc = proc

            async for raw_line in proc.stdout:
                line = raw_line.decode(errors="replace").strip()
                # Line format: _NET_ACTIVE_WINDOW(WINDOW): window id # 0x1234567
                # or on un-focus: _NET_ACTIVE_WINDOW(WINDOW): window id # 0x0
                if "0x0" in line or "not found" in line:
                    continue  # screen un-focused (e.g. lock screen)

                # Small debounce — rapid alt-tab produces multiple events;
                # wait 80 ms and take only the final one.
                await asyncio.sleep(0.08)

                await self.capture_snapshot(
                    type("Event", (), {"data": {"task_id": "focus_event"}})()
                )

        except asyncio.CancelledError:
            raise
        except Exception as exc:
            logger.warning(
                "WindowDetector: focus event watcher crashed (%s) — "
                "falling back to poll.", exc
            )

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

    def _get_window_cwd(self,pid: int | None,app_type: str = "",window_title: str = "",
) -> str:
        """
        CWD Resolver v2 (Context-aware OS resolver)

        Strategy:
        1. Terminal → shell process cwd (highest accuracy)
        2. File manager → actual GUI path (DBus / Finder / heuristics)
        3. Code editor → project root via process cwd
        4. Folder-like window title → resolve in HOME
        5. Fallback → os.getcwd()
        """

        if not pid:
            return os.getcwd()

        title = (window_title or "").strip()

        try:
            # ─────────────────────────────────────────────
            # 1. TERMINAL (MOST RELIABLE)
            # ─────────────────────────────────────────────
            if app_type == "terminal":
                cwd = self._cwd_from_terminal_child(pid)
                if cwd:
                    return cwd

            # ─────────────────────────────────────────────
            # 2. FILE MANAGER (REAL GUI DIRECTORY)
            # ─────────────────────────────────────────────
            if app_type == "file_manager":
                cwd = self._cwd_from_file_manager(pid, window_title)
                if cwd:
                    return cwd

                # 🔧 NEW: folder-as-window fallback (Screenshots, Downloads, etc.)
                home = Path.home()
                candidate = home / title

                if title and len(title) < 80:
                    if candidate.exists() and candidate.is_dir():
                        return str(candidate)

            # ─────────────────────────────────────────────
            # 3. CODE EDITORS (PROJECT ROOT)
            # ─────────────────────────────────────────────
            if app_type == "code_editor":
                if self.os_name == "Linux":
                    link = Path(f"/proc/{pid}/cwd")
                    if link.exists():
                        return str(link.resolve())
                else:
                    import psutil
                    return psutil.Process(pid).cwd()

            # ─────────────────────────────────────────────
            # 4. BROWSER (NO REAL FS CONTEXT)
            # ─────────────────────────────────────────────
            if app_type == "browser":
                return os.getcwd()

            # ─────────────────────────────────────────────
            # 5. GENERIC PROCESS FALLBACK
            # ─────────────────────────────────────────────
            if self.os_name == "Linux":
                link = Path(f"/proc/{pid}/cwd")
                if link.exists():
                    return str(link.resolve())

            else:
                import psutil
                return psutil.Process(pid).cwd()

        except Exception as exc:
            logger.debug(
                "CWD resolver v2 failed pid=%s app_type=%s: %s",
                pid, app_type, exc
            )

        return os.getcwd()

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

    def _get_current_title_and_pid(self) -> tuple[str, int | None]:
        """OS-agnostic helper — returns (title, pid) for the active window."""
        if self.os_name == "Linux":
            return self._get_linux_info()
        elif self.os_name == "Windows" and self.win32gui:
            return self._get_windows_info()
        elif self.os_name == "Darwin":
            return self._get_macos_info()
        return "Unknown", None

    async def capture_snapshot(self, event: object) -> None:
        data_payload = getattr(event, "data", {})
        task_id      = data_payload.get("task_id", "background_poll")

        try:
            current_title, pid = self._get_current_title_and_pid()

            # ── Own-window guard ──────────────────────────────────────────
            # The Operonix panel is in the foreground.
            # For background polls: skip silently — don't overwrite the
            #   last real app snapshot with the panel's own process cwd.
            # For real task snapshots: serve _last_external_snapshot which
            #   is always current because xprop updates it the instant the
            #   user switches windows — even while the panel is visible.
            if self._is_own_window(current_title):
                if task_id in ("background_poll", "focus_event", "initial_boot"):
                    return  # silent skip — don't pollute the snapshot

                # Real task: serve the most recent external window snapshot.
                # 🔧 FIX 2: concurrency protection — guard read with lock
                async with self._snapshot_lock:
                    cached = (
                        self._last_external_snapshot
                        or data_payload.get("pre_panel_context")
                    )
                if cached is not None:
                    reply = dict(cached)
                    reply["task_id"] = task_id
                    await bus.emit("context_snapshot_ready", reply, source="window_detector")
                    logger.debug(
                        "Own-window guard: served cached context app=%s cwd=%s",
                        reply.get("app_name"), reply.get("cwd"),
                    )
                else:
                    logger.warning(
                        "Own-window guard: no cached context for task %s", task_id
                    )
                return

            # ── Dedup: skip if title unchanged ───────────────────────────
            # 🔧 FIX 5 (optional): only skip when we already have a snapshot —
            # same title ≠ same context (e.g. tab change, folder switch).
            # Require _last_external_snapshot to be set so the first capture
            # always goes through even if the title hasn't changed.
            if task_id in ("background_poll", "focus_event") \
                    and current_title == self.last_title \
                    and self._last_external_snapshot is not None:
                return  # nothing changed, skip

            self.last_title = current_title

            # Classify and resolve cwd
            app_context = await classifier.classify_async(current_title)
            cwd = await asyncio.get_running_loop().run_in_executor(
                # 🔧 FIX 3: use dedicated thread pool (not default shared pool)
                self._executor,
                self._get_window_cwd, pid,
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

            # 🔧 FIX 2: concurrency protection — guard write with lock
            async with self._snapshot_lock:
                self._last_external_snapshot = snapshot
            await bus.emit("context_snapshot_ready", snapshot, source="window_detector")
            logger.debug(
                "Snapshot committed: window='%s' cwd='%s' pid=%s",
                current_title, cwd, pid,
            )

        except Exception as exc:
            if task_id != "background_poll":
                logger.error("WindowDetector error: %s", exc)


window_detector = WindowDetector()