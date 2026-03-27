import platform
import asyncio
from core.event_bus import bus

class WindowDetector:
    def __init__(self):
        self.os_name = platform.system()
        # Initialize OS-specific libraries only when needed
        self._setup_os_imports()

    def _setup_os_imports(self):
        """Lazy load OS libraries to prevent crashes on different systems."""
        try:
            if self.os_name == "Windows":
                import win32gui, win32process
                self.win32gui = win32gui
            elif self.os_name == "Linux":
                from ewmh import EWMH
                self.ewmh = EWMH()
        except ImportError as e:
            print(f"⚠️ WindowDetector: Missing library for {self.os_name}: {e}")

    async def start(self):
        bus.subscribe("request_context_snapshot", self.capture_snapshot)
        print(f"🌍 Window Detector: Active on {self.os_name}")

    async def capture_snapshot(self, event):
        task_id = event.data.get("task_id")
        
        try:
            data = None
            if self.os_name == "Windows":
                data = self._get_windows_window()
            elif self.os_name == "Darwin":
                data = self._get_macos_window()
            elif self.os_name == "Linux":
                data = self._get_linux_window()

            if data:
                # Add task_id and metadata
                data["task_id"] = task_id
                
                # ✅ ARCHITECTURE CHECK: 
                # Instead of classifying here, we emit the raw data.
                # The Orchestrator will route this to app_classifier.py
                await bus.emit("raw_context_detected", data, source="window_detector")
            else:
                await bus.emit("context_snapshot_failed", {"task_id": task_id}, source="window_detector")

        except Exception as e:
            await bus.emit("task_failed", {"task_id": task_id, "error": f"Window Detection Error: {str(e)}"}, source="window_detector")

    def _get_windows_window(self):
        hwnd = self.win32gui.GetForegroundWindow()
        title = self.win32gui.GetWindowText(hwnd)
        rect = self.win32gui.GetWindowRect(hwnd)
        return {"window_title": title, "bounds": {"x": rect[0], "y": rect[1]}, "os": "windows"}

    def _get_macos_window(self):
        # Placeholder for your AppKit logic
        return {"window_title": "macOS App", "bounds": {"x": 0, "y": 0}, "os": "macos"}

    def _get_linux_window(self):
        win = self.ewmh.getActiveWindow()
        if win:
            return {"window_title": win.get_wm_name(), "bounds": {"x": 0, "y": 0}, "os": "linux"}
        return None

window_detector = WindowDetector()