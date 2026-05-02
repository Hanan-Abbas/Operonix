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
import shutil
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

        # _last_external_snapshot: always the most recent non-panel window.
        # Updated by every focus_event and background_poll that passes the
        # own-window guard. xprop overwrites this when VS Code gains focus
        # because the panel click focuses VS Code in X11.

        # _last_real_context: the last window the user was INTENTIONALLY
        # working in — never overwritten while the panel is the target.
        # Updated only when focus changes to a non-panel window AND the
        # previous focus was also a non-panel window (i.e. not a panel click).
        # input_adapter reads this to restore context before a query runs.
        self._last_real_context: dict | None = None
        # True while the Operonix panel window is visible.
        # Set by panel_opened / panel_closed events subscribed in start().
        # Used to block fake VS Code focus events caused by panel clicks.
        self._panel_visible: bool = False
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

        # Track panel visibility to block fake VS Code focus events.
        # When the panel is visible and user clicks into it, X11 fires
        # a focus_event for VS Code — _panel_visible=True blocks that
        # from overwriting _last_real_context.
        bus.subscribe("panel_opened", self._on_panel_visible)
        bus.subscribe("panel_closed", self._on_panel_hidden_flag)
        bus.subscribe("panel_hidden", self._on_panel_hidden_flag)

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

    async def _poll_loop(self) -> None:
        """Fallback: Windows/macOS or xprop crash recovery. 1s interval."""
        while True:
            await self.capture_snapshot(
                type("Event", (), {"data": {"task_id": "background_poll"}})()
            )
            await asyncio.sleep(1)

    async def _on_panel_visible(self, event: object) -> None:
        """Panel became visible — block fake VS Code focus events."""
        self._panel_visible = True
        logger.debug("WindowDetector: panel_visible=True")

    async def _on_panel_hidden_flag(self, event: object) -> None:
        """Panel hidden — resume updating _last_real_context."""
        self._panel_visible = False
        logger.debug("WindowDetector: panel_visible=False")

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

    def _infer_file_manager(self, pid: int | None, title: str) -> str:
        """
        Detect when a window is a file manager showing a folder, even though
        the classifier returned 'unknown' (because the title is just a folder
        name like 'Screenshots' with no recognisable app-name signal).

        Strategy:
          1. Check the process name of the window's PID — if it's a known
             file manager binary, return 'file_manager'.
          2. Title heuristic: single word, no file extension, no separator
             (dash/pipe/bracket) → likely a folder name → 'file_manager'.
        Returns the original 'unknown' if neither test passes.
        """
        _FILE_MANAGER_BINARIES = {
            "nautilus", "thunar", "nemo", "dolphin", "pcmanfm",
            "caja", "krusader", "ranger", "yazi", "midnight-commander",
        }

        # Check process name
        if pid:
            try:
                comm_path = Path(f"/proc/{pid}/comm")
                if comm_path.exists():
                    proc_name = comm_path.read_text().strip().lower()
                    if proc_name in _FILE_MANAGER_BINARIES:
                        logger.debug(
                            "_infer_file_manager: pid=%s proc=%s → file_manager",
                            pid, proc_name,
                        )
                        return "file_manager"
            except Exception:
                pass

        # Title heuristic: bare word with no extension or separator
        # e.g. 'Screenshots', 'Pictures', 'Downloads', 'Documents'
        t = title.strip()
        has_separator = any(c in t for c in ("-", "–", "—", "|", "[", "("))
        has_extension = "." in t.split()[-1] if t.split() else False
        is_short_word = len(t.split()) <= 2 and len(t) <= 40
        if is_short_word and not has_separator and not has_extension:
            logger.debug(
                "_infer_file_manager: title=%r looks like folder name → file_manager",
                t,
            )
            return "file_manager"

        return "unknown"

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
          1. /proc/<pid>/fd scan — read symlinks of open file descriptors;
             the file manager holds an open fd to the directory it shows.
             This works for ALL file managers with zero D-Bus dependency.
          2. Nautilus D-Bus via gio info on the active window URI.
          3. Window title starts with '/' — full path (Thunar, Nemo).
          4. Match title word against XDG standard dirs + bounded find.
        """
        if self.os_name == "Linux":
            # Strategy 1: /proc/<pid>/fd scan
            # The file manager keeps an open fd to the directory it's showing.
            # We find the fd symlink that points to an existing directory
            # under the user's home — that's the browsed folder.
            if pid:
                try:
                    home = str(Path.home())
                    fd_dir = Path(f"/proc/{pid}/fd")
                    if fd_dir.exists():
                        candidates: list[str] = []
                        for fd_link in fd_dir.iterdir():
                            try:
                                target = str(fd_link.resolve())
                                if (
                                    Path(target).is_dir()
                                    and target.startswith(home)
                                    and target != home
                                    and "proc" not in target
                                ):
                                    candidates.append(target)
                            except Exception:
                                continue
                        if candidates:
                            # Prefer the path whose name matches the window title
                            title_clean = window_title.strip()
                            for c in candidates:
                                if Path(c).name == title_clean:
                                    logger.debug(
                                        "_cwd_from_file_manager: fd match: %s", c
                                    )
                                    return c
                            # No title match — return deepest path (most specific)
                            best = max(candidates, key=lambda p: p.count("/"))
                            logger.debug(
                                "_cwd_from_file_manager: fd best: %s", best
                            )
                            return best
                except Exception as exc:
                    logger.debug("fd scan failed for pid=%s: %s", pid, exc)

            # Strategy 2: Nautilus D-Bus via gio
            try:
                # 'gio' is the GNOME I/O library CLI — reliable on Ubuntu/Fedora
                result = subprocess.run(
                    ["gdbus", "call", "--session",
                     "--dest", "org.gnome.Nautilus",
                     "--object-path", "/org/gnome/Nautilus",
                     "--method", "org.gnome.Nautilus.Application.OpenLocations"],
                    capture_output=True, text=True, timeout=1,
                )
                # Try the simpler 'xdg-open --print-reply' approach
                gio_result = subprocess.run(
                    ["gio", "info", "-a", "standard::target-uri",
                     f"computer:///{pid}"],
                    capture_output=True, text=True, timeout=1,
                )
                # Parse any file:// URI in output
                for output in [result.stdout, gio_result.stdout]:
                    if "file://" in output:
                        import urllib.parse
                        raw = output.split("file://")[1].split("'")[0].split('"')[0]
                        decoded = urllib.parse.unquote(raw).strip()
                        if decoded and Path(decoded).is_dir():
                            return decoded
            except Exception:
                pass

            # Strategy 3: title is a full path
            candidate = window_title.strip().split(" ")[0]
            if candidate.startswith("/") and Path(candidate).is_dir():
                return candidate

            # Strategy 4: match title word against XDG dirs + bounded find
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

    async def capture_snapshot(self, event: object) -> None:
        data_payload = getattr(event, "data", {})
        task_id      = data_payload.get("task_id", "background_poll")

        try:
            current_title, pid = await self._get_current_title_and_pid()

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
            # Applies to both background_poll and focus_event task IDs.
            # We commit every new non-own-window title immediately.
            if task_id in ("background_poll", "focus_event") \
                    and current_title == self.last_title:
                return  # nothing changed, skip

            self.last_title = current_title

            # Classify and resolve cwd
            app_context = await classifier.classify_async(current_title)

            # Nautilus (and Thunar/Nemo/Dolphin) set the window title to
            # the name of the folder being browsed — e.g. "Screenshots",
            # "Pictures", "Downloads".  The classifier returns 'unknown'
            # because there are no app-name signals in a plain folder name.
            # Detect this: low/unknown confidence + no separator in title
            # + pid maps to a known file manager process name.
            resolved_app_type = app_context.category
            if resolved_app_type == "unknown" and current_title:
                resolved_app_type = self._infer_file_manager(pid, current_title)

            cwd = await asyncio.get_running_loop().run_in_executor(
                None, self._get_window_cwd, pid,
                resolved_app_type, current_title,
            )

            snapshot = {
                "window_title": current_title,
                "app_name":    app_context.app_name,
                "app_type":    resolved_app_type,  # may be overridden above
                "sub_context": app_context.sub_context,
                "confidence":  app_context.confidence,
                "llm_used":    app_context.llm_used,
                "app_context": app_context.to_dict(),
                "cwd":         cwd,
                "window_pid":  pid,
                "task_id":     task_id,
            }

            self._last_external_snapshot = snapshot

            # _last_real_context tracks the last window the user was
            # intentionally working in.
            #
            # Rule: update ONLY when the panel is NOT visible.
            # When the panel is visible and the user clicks into it,
            # X11 fires a focus_event for VS Code (the panel's X11 parent).
            # That fake focus event must NOT overwrite the real context
            # (e.g. Screenshots) that was active before the panel click.
            #
            # _panel_visible is set True by panel_opened and False by
            # panel_closed — subscribed in start() below.
            if not self._panel_visible:
                last_real_title = (
                    self._last_real_context.get("window_title")
                    if self._last_real_context else None
                )
                if current_title != last_real_title:
                    self._last_real_context = dict(snapshot)
                    logger.debug(
                        "Real context updated: window='%s' cwd='%s'",
                        current_title, cwd,
                    )

            await bus.emit("context_snapshot_ready", snapshot, source="window_detector")
            logger.debug(
                "Snapshot committed: window='%s' cwd='%s' pid=%s",
                current_title, cwd, pid,
            )

        except Exception as exc:
            if task_id != "background_poll":
                logger.error("WindowDetector error: %s", exc)


window_detector = WindowDetector()