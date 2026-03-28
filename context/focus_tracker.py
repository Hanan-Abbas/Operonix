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

    