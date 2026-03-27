import platform
import asyncio
import subprocess
from core.event_bus import bus
from context.app_classifier import classifier

class WindowDetector:
    def __init__(self):
        self.os_name = platform.system()
        self.ewmh = None
        self.win32gui = None
        self.AppKit = None # For Mac
        self.Quartz = None # For Mac
        self._setup_os_imports()

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
            elif self.os_name == "Darwin": # 🍎 macOS
                try:
                    from AppKit import NSWorkspace
                    from Quartz import CGWindowListCopyWindowInfo, kCGWindowListOptionOnScreenOnly, kCGNullWindowID
                    self.NSWorkspace = NSWorkspace
                    self.CGWindowListCopyWindowInfo = CGWindowListCopyWindowInfo
                except ImportError:
                    print("⚠️ WindowDetector: Mac libraries (pyobjc) missing.")
        except Exception as e:
            print(f"⚠️ WindowDetector Setup Error: {e}")

    async def start(self):
        bus.subscribe("request_context_snapshot", self.capture_snapshot)
        print(f"🌍 Window Detector: Active on {self.os_name}")
        await asyncio.sleep(1)
        await self.capture_snapshot(type('Event', (object,), {'data': {'task_id': 'initial_boot'}})())
        asyncio.create_task(self._poll_loop())

    async def _poll_loop(self):
        while True:
            await self.capture_snapshot(type('Event', (object,), {'data': {}})())
            await asyncio.sleep(2)

    def _get_linux_title(self):
        try:
            return subprocess.check_output(["xdotool", "getactivewindow", "getwindowname"], 
                                         stderr=subprocess.STDOUT).decode("utf-8").strip()
        except Exception:
            try:
                if self.ewmh:
                    win = self.ewmh.getActiveWindow()
                    if win:
                        name = self.ewmh.get_wm_name(win) if hasattr(self.ewmh, 'get_wm_name') else self.ewmh.getWMName(win)
                        return name.decode('utf-8') if isinstance(name, bytes) else name
            except: pass
        return "Unknown Linux Window"

    def _get_macos_title(self):
        """🍎 Fetches the active window title on macOS."""
        try:
            curr_app = self.NSWorkspace.sharedWorkspace().frontmostApplication()
            curr_pid = curr_app.processIdentifier()
            options = 1 << 0 # kCGWindowListOptionOnScreenOnly
            window_list = self.CGWindowListCopyWindowInfo(options, 0) # kCGNullWindowID
            
            for window in window_list:
                if window['kCGWindowOwnerPID'] == curr_pid:
                    return window.get('kCGWindowName', curr_app.localizedName())
            return curr_app.localizedName()
        except Exception:
            return "Unknown Mac Window"

    async def capture_snapshot(self, event):
        data_payload = getattr(event, 'data', {})
        task_id = data_payload.get("task_id", "background_poll")
        snapshot = {"window_title": "Unknown", "app_type": "unknown", "task_id": task_id}
        
        try:
            if self.os_name == "Linux":
                snapshot["window_title"] = self._get_linux_title()
            elif self.os_name == "Windows" and self.win32gui:
                hwnd = self.win32gui.GetForegroundWindow()
                snapshot["window_title"] = self.win32gui.GetWindowText(hwnd)
            elif self.os_name == "Darwin": # 🍎 Mac logic
                snapshot["window_title"] = self._get_macos_title()

            snapshot["app_type"] = classifier.classify(snapshot["window_title"])
            await bus.emit("context_snapshot_ready", snapshot, source="window_detector")
            
        except Exception as e:
            if task_id != "background_poll":
                print(f"❌ WindowDetector Error: {e}")

window_detector = WindowDetector()