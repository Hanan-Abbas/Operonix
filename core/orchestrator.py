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
        
        # 🔄 FIX: Mapper outputs 'capability_mapped'. 
        # We route this to the Decision Engine first!
        bus.subscribe("capability_mapped", self.route_to_decision_engine)
        
        # 🔄 FIX: Decision Engine determines the tool and outputs 'request_planning'
        bus.subscribe("request_planning", self.route_to_planner)
        
        # 🔄 FIX: Planner finishes building steps and outputs 'task_dispatched'
        bus.subscribe("task_dispatched", self.route_to_executor)
        
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
        await bus.emit("request_context_snapshot", {"task_id": task_id}, source="orchestrator")

        # STEP 2: Send to brain/llm_client.py for parsing
        await bus.emit("request_intent_parsing", {
            "task_id": task_id,
            "text": user_text
        }, source="orchestrator")

    async def route_to_mapper(self, event):
        """Phase 2: Intent -> Capability Mapping."""
        await bus.emit("request_capability_mapping", event.data, source="orchestrator")

    # 🔗 NEW: Added middleman to let Decision Engine prioritize & route
    async def route_to_decision_engine(self, event):
        """Phase 2.5: Enqueue and determine optimal execution tool."""
        # The Decision Engine handles queueing and tool fallback strategies.
        # We don't need to manually emit here because the Decision Engine 
        # subscribes to 'capability_mapped' directly!
        pass

    async def route_to_planner(self, event):
        """Phase 3: Capability -> Step-by-Step Plan."""
        # This pass is handled directly since Planner listens to 'request_planning'
        # in the new structure! I am keeping the method placeholder for your logs.
        pass

    async def route_to_executor(self, event):
        """Phase 4: Plan -> Real-world Action."""
        # 🔄 FIX: Planner fires 'task_dispatched'. We grab it and request execution!
        await bus.emit("request_execution", event.data, source="orchestrator")

    async def handle_failure(self, event):
        """Phase 5: Self-Healing & Debugging."""
        task_id = event.data.get("task_id")
        error = event.data.get("error")
        
        print(f"❌ Task [{task_id}] failed: {error}")
        
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