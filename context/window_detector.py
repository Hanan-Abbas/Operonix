import platform
import asyncio
import subprocess
from core.event_bus import bus
from context.app_classifier import classifier   # ← unchanged import

# ── Own-window identifiers ────────────────────────────────────────────────────
# Any active window whose title contains one of these strings (case-insensitive)
# is considered part of the Operonix UI itself.  The detector will NOT emit a
# context_snapshot_ready for such windows during background polls — the last
# known *external* context is preserved instead.
_OWN_WINDOW_SUBSTRINGS = [
    "operonix",
    "panel",               # matches "Operonix Panel", "Command Panel", etc.
    "command panel",
]


class WindowDetector:
    def __init__(self):
        self.os_name = platform.system()
        self.ewmh = None
        self.win32gui = None
        self.last_title = None

        # FIX (Bug 2): cache the last window context that was NOT our own panel.
        # When the user clicks into the panel, we keep serving this cached value
        # so the orchestrator's app_context_changed loop and any snapshot consumer
        # always see the real app the user was working in.
        self._last_external_snapshot: dict | None = None

        self._setup_os_imports()

    # ── helpers ───────────────────────────────────────────────────────────────

    def _is_own_window(self, title: str) -> bool:
        """Return True if *title* belongs to the Operonix panel / UI."""
        t = (title or "").lower()
        return any(sub in t for sub in _OWN_WINDOW_SUBSTRINGS)

    def _setup_os_imports(self):
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
                    print("⚠️  WindowDetector: Mac libraries (pyobjc) missing.")
        except Exception as e:
            print(f"⚠️  WindowDetector Setup Error: {e}")

    async def start(self):
        bus.subscribe("request_context_snapshot", self.capture_snapshot)
        print(f"🌍 Window Detector: Active on {self.os_name}")
        await asyncio.sleep(1)
        await self.capture_snapshot(
            type('Event', (object,), {'data': {'task_id': 'initial_boot'}})()
        )
        asyncio.create_task(self._poll_loop())

    async def _poll_loop(self):
        while True:
            await self.capture_snapshot(
                type('Event', (object,), {'data': {'task_id': 'background_poll'}})()
            )
            await asyncio.sleep(2)

    # ── OS-specific title fetchers ────────────────────────────────────────────

    def _get_linux_title(self) -> str:
        try:
            return subprocess.check_output(
                ["xdotool", "getactivewindow", "getwindowname"],
                stderr=subprocess.STDOUT
            ).decode("utf-8").strip()
        except Exception:
            try:
                if self.ewmh:
                    win = self.ewmh.getActiveWindow()
                    if win:
                        name = (self.ewmh.get_wm_name(win)
                                if hasattr(self.ewmh, 'get_wm_name')
                                else self.ewmh.getWMName(win))
                        return name.decode('utf-8') if isinstance(name, bytes) else name
            except Exception:
                pass
        return "Unknown Linux Window"

    def _get_macos_title(self) -> str:
        try:
            curr_app = self.NSWorkspace.sharedWorkspace().frontmostApplication()
            curr_pid = curr_app.processIdentifier()
            window_list = self.CGWindowListCopyWindowInfo(1 << 0, 0)
            for window in window_list:
                if window['kCGWindowOwnerPID'] == curr_pid:
                    return window.get('kCGWindowName', curr_app.localizedName())
            return curr_app.localizedName()
        except Exception:
            return "Unknown Mac Window"

    # ── Snapshot capture ──────────────────────────────────────────────────────

    async def capture_snapshot(self, event):
        data_payload = getattr(event, 'data', {})
        task_id = data_payload.get("task_id", "background_poll")

        current_title = "Unknown"

        try:
            # 1. Fetch the active window title
            if self.os_name == "Linux":
                current_title = self._get_linux_title()
            elif self.os_name == "Windows" and self.win32gui:
                hwnd = self.win32gui.GetForegroundWindow()
                current_title = self.win32gui.GetWindowText(hwnd)
            elif self.os_name == "Darwin":
                current_title = self._get_macos_title()

            # ── FIX (Bug 2): Own-window guard ─────────────────────────────────
            # If the user clicked into the Operonix panel, the foreground title
            # will match one of our own identifiers.  For background polls we
            # silently serve the last external snapshot so no context_changed
            # event is emitted and the task in flight keeps its original context.
            # For explicit snapshots (task-triggered), we still serve the cached
            # external context rather than the panel window.
            if self._is_own_window(current_title):
                if self._last_external_snapshot is not None:
                    # Re-emit the last good external snapshot so explicit
                    # request_context_snapshot callers still get a response.
                    if task_id != "background_poll":
                        cached = dict(self._last_external_snapshot)
                        cached["task_id"] = task_id   # keep task_id current
                        await bus.emit(
                            "context_snapshot_ready", cached, source="window_detector"
                        )
                # Either way: do NOT update last_title or emit a new snapshot
                # that would overwrite the real app context.
                return
            # ── end fix ───────────────────────────────────────────────────────

            # 2. Skip if nothing changed during background polls
            if current_title == self.last_title and task_id == "background_poll":
                return

            self.last_title = current_title

            # 3. Classify — use async path so low-confidence titles can fall
            #    back to the LLM without blocking the poll loop.
            app_context = await classifier.classify_async(current_title)

            snapshot = {
                "window_title": current_title,
                "app_name":    app_context.app_name,
                "app_type":    app_context.category,
                "sub_context": app_context.sub_context,
                "confidence":  app_context.confidence,
                "llm_used":    app_context.llm_used,
                "app_context": app_context.to_dict(),
                "task_id":     task_id,
            }

            # Cache this as the last known external (non-panel) snapshot.
            self._last_external_snapshot = snapshot

            await bus.emit("context_snapshot_ready", snapshot, source="window_detector")

        except Exception as e:
            if task_id != "background_poll":
                print(f"❌ WindowDetector Error: {e}")


window_detector = WindowDetector()