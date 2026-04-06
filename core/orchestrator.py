import os
import sys
import asyncio
import uuid
import logging
from core.event_bus import bus
from core.config import settings

# Import your voice modules
from voice.wake_word import WakeWordDetector
from voice.listener import VoiceListener
from voice.audio_manager import AudioManager
from voice.pipeline import VoicePipeline

class Orchestrator:
    def __init__(self):
        self.active_tasks = {}
        self.is_running = False
        self.logger = logging.getLogger("Orchestrator")
        
        # 🟢 FIX: Create the AudioManager ONLY ONCE
        self.audio_manager = AudioManager(
            rate=getattr(settings, "AUDIO_RATE", 16000),
            chunk=getattr(settings, "AUDIO_CHUNK", 1280),
            auto_start=True
        )
        
        # 🟢 FIX: Pass that single manager to all sub-modules
        self.pipeline = VoicePipeline(audio_manager=self.audio_manager)
        
        wake_phrase = getattr(settings, "WAKE_WORD", "alexa")
        self.wake_detector = WakeWordDetector(
            wake_word=wake_phrase, 
            audio_manager=self.audio_manager
        )
        
        self.listener = VoiceListener(audio_manager=self.audio_manager)

    async def start(self):
        """Initialize the core loop and subscribe to the pipeline events."""
        self.is_running = True
        
        # Subscriptions
        bus.subscribe("wake_word_detected", self.handle_wake_word)
        bus.subscribe("user_input_received", self.handle_new_task)
        bus.subscribe("intent_parsed", self.route_to_mapper)
        bus.subscribe("capability_mapped", self.route_to_decision_engine)
        bus.subscribe("task_dispatched", self.route_to_executor)
        bus.subscribe("task_completed", self.finalize_task)
        bus.subscribe("task_failed", self.handle_failure)
        
        self.logger.info("🎛️ Orchestrator: System Backbone Online. Awaiting commands.")
        asyncio.create_task(self.background_wake_word_listener())

    async def background_wake_word_listener(self):

        self.logger.info("🎙️ Orchestrator: Starting background wake word engine...")
        loop = asyncio.get_running_loop()
        self.wake_detector.loop = loop
        while self.is_running:
            # Use the shared manager via the detector
            await loop.run_in_executor(None, self.wake_detector.detect)
            await asyncio.sleep(0.1)

    async def handle_wake_word(self, event):
        """Fires when the user says the wake word."""
        trigger = event.data.get("trigger")
        self.logger.info(f"\n🔔 Orchestrator: System woken up by '{trigger}'!")

        # 🟢 FIX: Use the listener to capture command in a thread
        loop = asyncio.get_running_loop()
        command_text = await loop.run_in_executor(None, self.listener.listen_until_silent)
        
        if command_text:
            # ONLY emit this. The standard pipeline takes it from here.
            await bus.emit("user_input_received", {"text": command_text}, source="orchestrator")
        else:
            self.logger.warning("🔇 Orchestrator: No voice command understood.")

    # ... keep handle_new_task and other routing methods as they were ...

    async def handle_new_task(self, event):
        """Phase 1: Initialization & Context Gathering."""
        task_id = str(uuid.uuid4())[:8]
        user_text = event.data.get("text")
        
        self.active_tasks[task_id] = {
            "status": "gathering_context",
            "input": user_text,
            "context": {}
        }

        self.logger.info(f"🎛️ Task [{task_id}] Initialized: '{user_text}'")

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
        
        self.logger.error(f"❌ Task [{task_id}] failed: {error}")
        
        await bus.emit("error_detected", {
            "task_id": task_id,
            "error": error,
            "context": self.active_tasks.get(task_id, {}).get("context")
        }, source="orchestrator")

    async def finalize_task(self, event):
        task_id = event.data.get("task_id")
        self.logger.info(f"✅ Task [{task_id}] Completed Successfully.")
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