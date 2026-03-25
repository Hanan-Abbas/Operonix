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

    async def handle_new_task(self, event):
        """The entry point for any user request."""
        task_id = str(uuid.uuid4())[:8]
        user_text = event.data.get("text")
        
        print(f"🎛️ Orchestrator: New task [{task_id}] -> '{user_text}'")
        
        self.active_tasks[task_id] = {
            "status": "initializing",
            "input": user_text
        }

        # STEP 1: Get Environment Context (What app is open?)
        await bus.emit("request_context_snapshot", {"task_id": task_id}, source="orchestrator")

        # STEP 2: Send to Brain for Intent Parsing
        # We pass the text to the brain to turn "make a file" into JSON
        await bus.emit("request_intent_parsing", {
            "task_id": task_id,
            "text": user_text
        }, source="orchestrator")

    