import asyncio
import platform
import os
from core.event_bus import bus

class Executor:
    def __init__(self):
        self.os_name = platform.system()
        self.is_running = False
        # Mapping tool names to their class instances (to be loaded later)
        self.tools = {} 

    async def start(self):
        """Subscribe to plans coming from the Planner."""
        bus.subscribe("plan_ready", self.execute_plan)
        self.is_running = True
        print(f"⚙️ Executor: Online. Operating System: {self.os_name}")

    