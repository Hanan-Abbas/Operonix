import asyncio
import uuid
import logging
from core.event_bus import bus

class Orchestrator:
    def __init__(self):
        self.active_tasks = {}
        self.is_running = False
        self.logger = logging.getLogger("Orchestrator")

    async def start(self):
        """Initialize the core loop and subscribe to the pipeline events."""
        self.is_running = True
        
        # 1. Entry Point: User speaks or types
        bus.subscribe("user_input_received", self.handle_new_task)
        
        # 2. Pipeline Tracking: Monitor the journey from Brain to Hand
        bus.subscribe("intent_parsed", self.route_to_mapper)
        bus.subscribe("capability_mapped", self.route_to_planner)
        bus.subscribe("plan_ready", self.route_to_executor)
        
        # 3. Lifecycle: Success and Error handling
        bus.subscribe("task_completed", self.finalize_task)
        bus.subscribe("task_failed", self.handle_failure)
        
        print("🎛️ Orchestrator: System Backbone Online. Awaiting commands.")

    async def handle_new_task(self, event):
        """Phase 1: Initialization & Context Gathering."""
        task_id = str(uuid.uuid4())[:8]
        user_text = event.data.get("text")
        
        # Store metadata for the Dashboard / Session Memory
        self.active_tasks[task_id] = {
            "status": "gathering_context",
            "input": user_text,
            "context": {}
        }

        print(f"🎛️ Task [{task_id}] Initialized: '{user_text}'")

        # STEP 1: Ask context/window_detector.py what app is currently focused
        # This is vital for your "Plugin -> Tool -> UI" priority logic
        await bus.emit("request_context_snapshot", {"task_id": task_id}, source="orchestrator")

        # STEP 2: Send to brain/llm_client.py for parsing
        await bus.emit("request_intent_parsing", {
            "task_id": task_id,
            "text": user_text
        }, source="orchestrator")

    async def route_to_mapper(self, event):
        """Phase 2: Intent -> Capability Mapping."""
        # After LLM Client parses intent, we send it to capability_mapper.py
        await bus.emit("request_capability_mapping", event.data, source="orchestrator")

    async def route_to_planner(self, event):
        """Phase 3: Capability -> Step-by-Step Plan."""
        # After Mapper validates we CAN do it, we send it to brain/planner.py
        # This is where 'Coding' logic or 'File' logic is generated
        await bus.emit("request_planning", event.data, source="orchestrator")

    async def route_to_executor(self, event):
        """Phase 4: Plan -> Real-world Action."""
        # The planner is done, now we hand the 'Steps' to executor/executor.py
        # The Executor will use tool_selector to choose between Shell, File, or UI.
        await bus.emit("request_execution", event.data, source="orchestrator")

    async def handle_failure(self, event):
        """Phase 5: Self-Healing & Debugging."""
        task_id = event.data.get("task_id")
        error = event.data.get("error")
        
        print(f"❌ Task [{task_id}] failed: {error}")
        
        # Per your structure: Trigger debugging/error_parser.py and auto_fix.py
        await bus.emit("error_detected", {
            "task_id": task_id,
            "error": error,
            "context": self.active_tasks.get(task_id, {}).get("context")
        }, source="orchestrator")

    async def finalize_task(self, event):
        task_id = event.data.get("task_id")
        print(f"✅ Task [{task_id}] Completed Successfully.")
        if task_id in self.active_tasks:
            del self.active_tasks[task_id]

# Global instance
orchestrator = Orchestrator()