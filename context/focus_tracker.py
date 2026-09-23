import platform
import asyncio
from core.event_bus import bus

class FocusTracker:
    def __init__(self):
        self.os_name = platform.system()
        self.active_hwnd = None # Windows handle
        self.active_pid = None  # Linux/Mac process ID
        self.last_known_title = ""

    async def start(self):
        # Listen for a request from the Executor to verify focus
        bus.subscribe("verify_focus_request", self.check_focus_alignment)
        print("🎯 Focus Tracker: Monitoring active window integrity.")

    async def check_focus_alignment(self, event):
        """
        Critical Safety Check: 
        Compares the 'Target Window' in the Plan vs. the 'Actual Window' on Screen.
        """
        task_id = event.data.get("task_id")
        expected_title = event.data.get("expected_title")
        
        current_title = await self._get_current_foreground_title()

        # Fuzzy matching because titles change (e.g., "file.py - VS Code" vs "file.py (unsaved) - VS Code")
        if expected_title.lower() in current_title.lower() or current_title.lower() in expected_title.lower():
            await bus.emit("focus_verified", {"task_id": task_id, "status": "match"}, source="focus_tracker")
        else:
            print(f"⚠️ FOCUS MISMATCH! Expected: {expected_title} | Actual: {current_title}")
            await bus.emit("focus_verified", {
                "task_id": task_id, 
                "status": "mismatch",
                "actual_window": current_title
            }, source="focus_tracker")

    def get_current_focus(self):
        """Synchronous wrapper for getting current focus information.
        
        Returns a dict with focus information for compatibility with observe.py.
        This is a synchronous version that can be called from non-async contexts.
        """
        try:
            import asyncio
            # Try to get the current running loop
            try:
                loop = asyncio.get_running_loop()
                # If we have a running loop, we need to run the async method
                # This is a simple fallback - in production you'd want proper async handling
                title = "Unknown"
            except RuntimeError:
                # No running loop, use synchronous approach
                title = self._get_sync_foreground_title()
            
            return {
                "element": title,
                "window": title,
                "timestamp": None
            }
        except Exception as e:
            return {
                "element": "Error",
                "window": "Error",
                "timestamp": None
            }

    def _get_sync_foreground_title(self):
        """Synchronous foreground window detection."""
        try:
            if self.os_name == "Windows":
                import win32gui
                return win32gui.GetWindowText(win32gui.GetForegroundWindow())
            elif self.os_name == "Linux":
                import subprocess
                return subprocess.check_output(["xdotool", "getactivewindow", "getwindowname"]).decode().strip()
            # MacOS logic would go here
            return "Unknown"
        except Exception:
            return "Error detection"

    async def _get_current_foreground_title(self):
        """OS-specific foreground window detection."""
        try:
            if self.os_name == "Windows":
                import win32gui
                return win32gui.GetWindowText(win32gui.GetForegroundWindow())
            elif self.os_name == "Linux":
                import subprocess
                return subprocess.check_output(["xdotool", "getactivewindow", "getwindowname"]).decode().strip()
            # MacOS logic would go here
            return "Unknown"
        except Exception:
            return "Error detection"

focus_tracker = FocusTracker()