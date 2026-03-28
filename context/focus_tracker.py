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

    