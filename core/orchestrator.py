import asyncio
import uuid
from core.event_bus import bus

class Orchestrator:
    def __init__(self):
        self.active_tasks = {}
        self.is_running = False

    async def start(self):
        """Initialize subscriptions and start the loop."""
        self.is_running = True
        
        # 1. Listen for new voice/text commands
        bus.subscribe("user_input_received", self.handle_new_task)
        
        # 2. Listen for task completion or failure
        bus.subscribe("task_completed", self.finalize_task)
        bus.subscribe("task_failed", self.handle_failure)
        
        print("🎛️ Orchestrator: Online and listening for commands...")

    