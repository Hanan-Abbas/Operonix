import platform
import asyncio
import subprocess
from core.event_bus import bus

class WindowDetector:
    def __init__(self):
        self.os_name = platform.system()
        self.ewmh = None
        self.win32gui = None
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
                    print("⚠️ WindowDetector: EWMH library missing, falling back to xdotool.")
        except Exception as e:
            print(f"⚠️ WindowDetector Setup Error: {e}")

    async def start(self):
        bus.subscribe("request_context_snapshot", self.capture_snapshot)
        print(f"🌍 Window Detector: Active on {self.os_name}")
        
        await asyncio.sleep(1)
        # Initial trigger
        await self.capture_snapshot(type('Event', (object,), {'data': {'task_id': 'initial_boot'}})())
        
        # Background polling task
        asyncio.create_task(self._poll_loop())

    async def _poll_loop(self):
        while True:
            await self.capture_snapshot(type('Event', (object,), {'data': {}})())
            await asyncio.sleep(2)

    def _get_linux_title(self):
        """Highly reliable title fetcher for Ubuntu/Linux."""
        try:
            # Try xdotool first (standard for Ubuntu automation)
            return subprocess.check_output(["xdotool", "getactivewindow", "getwindowname"], 
                                         stderr=subprocess.STDOUT).decode("utf-8").strip()
        except Exception:
            try:
                # Fallback to EWMH if xdotool isn't installed
                if self.ewmh:
                    win = self.ewmh.getActiveWindow()
                    if win:
                        name = self.ewmh.get_wm_name(win) if hasattr(self.ewmh, 'get_wm_name') else self.ewmh.getWMName(win)
                        return name.decode('utf-8') if isinstance(name, bytes) else name
            except:
                pass
        return "Unknown Linux Window"

    def _classify_app(self, title):
        """Simple internal classifier until AppClassifier is fully implemented."""
        t = title.lower()
        if "visual studio code" in t or ".py" in t: return "Code Editor"
        if "chrome" in t or "firefox" in t or "opera" in t: return "Browser"
        if "terminal" in t or "bash" in t: return "Terminal"
        return "General App"

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

            snapshot["app_type"] = self._classify_app(snapshot["window_title"])

            # Emit to the Bus
            await bus.emit("context_snapshot_ready", snapshot, source="window_detector")
            
        except Exception as e:
            if task_id != "background_poll":
                print(f"❌ WindowDetector Error: {e}")

window_detector = WindowDetector()