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

    