import os
import sys
import asyncio
import uuid
import logging
from core.event_bus import bus

# Import your voice modules
from voice.wake_word import WakeWordDetector
from voice.listener import VoiceListener

class Orchestrator:
    def __init__(self):
        self.active_tasks = {}
        self.is_running = False
        self.logger = logging.getLogger("Orchestrator")
        
        # Initialize voice components
        self.wake_detector = WakeWordDetector(wake_word="alexa")
        self.listener = VoiceListener()

    async def start(self):
        """Initialize the core loop and subscribe to the pipeline events."""
        self.is_running = True
        
        # 0. Voice Wake-up Pipeline
        bus.subscribe("wake_word_detected", self.handle_wake_word)
        bus.subscribe("voice_audio_captured", self.process_voice_to_input)
        
        # 1. Entry Point: User speaks or types
        bus.subscribe("user_input_received", self.handle_new_task)
        
        # 2. Pipeline Tracking: Monitor the journey from Brain to Hand
        bus.subscribe("intent_parsed", self.route_to_mapper)
        bus.subscribe("capability_mapped", self.route_to_decision_engine)
        bus.subscribe("request_planning", self.route_to_planner)
        bus.subscribe("task_dispatched", self.route_to_executor)
        
        # 3. Lifecycle: Success and Error handling
        bus.subscribe("task_completed", self.finalize_task)
        bus.subscribe("task_failed", self.handle_failure)
        
        print("🎛️ Orchestrator: System Backbone Online. Awaiting commands.")
        
        # Start background task to listen for the wake word
        asyncio.create_task(self.background_wake_word_listener())

    async def background_wake_word_listener(self):
        """Runs the blocking openWakeWord in a thread so it doesn't freeze the OS."""
        print("🎙️ Orchestrator: Starting background wake word engine...")
        loop = asyncio.get_running_loop()
        
        while self.is_running:
            # Run the blocking wake word loop in the executor
            await loop.run_in_executor(None, self.wake_detector.listen_for_wake_word)
            # Short pause before recycling the loop
            await asyncio.sleep(0.1)

    async def handle_wake_word(self, event):
        """Fires when the user says the wake word."""
        trigger = event.data.get("trigger")
        print(f"\n🔔 Orchestrator: System woken up by '{trigger}'!")
        
        # Run the voice listener to capture the user's command
        loop = asyncio.get_running_loop()
        command_text = await loop.run_in_executor(None, self.listener.listen_until_silent)
        
        # Emit the captured text to the event bus
        await bus.emit("voice_audio_captured", {"text": command_text}, source="orchestrator")

    async def process_voice_to_input(self, event):
        """Converts transcribed voice into the standard user input event."""
        text = event.data.get("text")
        if text:
            await bus.emit("user_input_received", {"text": text}, source="orchestrator")
        else:
            print("🔇 Orchestrator: No voice command understood.")

    async def handle_new_task(self, event):
        """Phase 1: Initialization & Context Gathering."""
        task_id = str(uuid.uuid4())[:8]
        user_text = event.data.get("text")
        
        self.active_tasks[task_id] = {
            "status": "gathering_context",
            "input": user_text,
            "context": {}
        }

        print(f"🎛️ Task [{task_id}] Initialized: '{user_text}'")

        # Context Snapshot Request
        await bus.emit("request_context_snapshot", {"task_id": task_id}, source="orchestrator")

        # Intent Parsing Request
        await bus.emit("request_intent_parsing", {
            "task_id": task_id,
            "text": user_text
        }, source="orchestrator")

    async def route_to_mapper(self, event):
        """Phase 2: Intent -> Capability Mapping."""
        await bus.emit("request_capability_mapping", event.data, source="orchestrator")

    async def route_to_decision_engine(self, event):
        """Phase 2.5: Enqueue and determine optimal execution tool."""
        pass

    async def route_to_planner(self, event):
        """Phase 3: Capability -> Step-by-Step Plan."""
        pass

    async def route_to_executor(self, event):
        """Phase 4: Plan -> Real-world Action."""
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

# Entry point for testing the orchestrator alone
if __name__ == "__main__":
    async def main():
        await orchestrator.start()
        # Keep the main loop alive
        while True:
            await asyncio.sleep(1)
            
    try:
        asyncio.run(main())
    except KeyboardInterrupt:
        print("\n🛑 Orchestrator stopped.")