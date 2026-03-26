import platform
import asyncio
import psutil
from core.event_bus import bus

class WindowDetector:
    def __init__(self):
        self.os_name = platform.system()
        print(f"🌍 Window Detector: Detected OS -> {self.os_name}")

    async def start(self):
        bus.subscribe("request_context_snapshot", self.capture_snapshot)
        print("🌍 Window Detector: Multi-OS listener active.")

    async def capture_snapshot(self, event):
        task_id = event.data.get("task_id")
        
        try:
            # Dynamically call the correct method based on OS
            if self.os_name == "Windows":
                data = self._get_windows_window()
            elif self.os_name == "Darwin": # macOS
                data = self._get_macos_window()
            else: # Linux
                data = self._get_linux_window()

            if data:
                data["task_id"] = task_id
                data["app_type"] = self._classify_app(data["window_title"])
                await bus.emit("context_snapshot_ready", data, source="window_detector")
                print(f"🌍 Context: {data['app_type']} focused ({data['window_title'][:20]}...)")
            else:
                await bus.emit("context_snapshot_failed", {"task_id": task_id}, source="window_detector")

        except Exception as e:
            await bus.emit("task_failed", {"task_id": task_id, "error": str(e)}, source="window_detector")

    def _get_windows_window(self):
        import win32gui, win32process
        hwnd = win32gui.GetForegroundWindow()
        title = win32gui.GetWindowText(hwnd)
        rect = win32gui.GetWindowRect(hwnd) # (left, top, right, bottom)
        return {"window_title": title, "bounds": {"x": rect[0], "y": rect[1]}}

    