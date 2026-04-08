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
        
        # 🟢 SINGLE SOURCE OF TRUTH: Create the AudioManager once
        self.audio_manager = AudioManager(
            rate=getattr(settings, "AUDIO_RATE", 16000),
            chunk=getattr(settings, "AUDIO_CHUNK", 1280),
            auto_start=True
        )
        
        # Pass the manager to all sub-modules
        self.pipeline = VoicePipeline(audio_manager=self.audio_manager)
        
        wake_phrase = getattr(settings, "WAKE_WORD", "alexa")
        self.wake_detector = WakeWordDetector(
            wake_word=wake_phrase, 
            audio_manager=self.audio_manager
        )
        
        # Note: If your listener.py doesn't have 'listen_until_silent', 
        # we'll use pipeline.capture_command below as it's more robust.
        self.listener = VoiceListener(audio_manager=self.audio_manager)

    async def start(self):
        """Initialize the core loop and background detection."""
        self.is_running = True
        
        # 1. FIX: Give the detector the current event loop so it can emit events
        self.wake_detector.loop = asyncio.get_running_loop()

        # 2. Subscriptions - FIX: Added missing handler to prevent AttributeError
        bus.subscribe("wake_word_detected", self.handle_wake_word)
        bus.subscribe("user_input_received", self.handle_user_input)

        # 3. Start the background thread for continuous listening
        # We use background_wake_word_listener because it uses an executor (thread)
        asyncio.create_task(self.background_wake_word_listener())
        self.logger.info("👂 Orchestrator: Wake word detection engine started. Listening for Alexa...")

    async def background_wake_word_listener(self):
        """Background task that keeps the detector running without blocking the main loop."""
        loop = asyncio.get_running_loop()
        while self.is_running:
            # We run detect() in an executor because it performs CPU-heavy ML inference
            await loop.run_in_executor(None, self.wake_detector.detect)
            # Minimal sleep to yield control back to the event loop
            await asyncio.sleep(0.01)

    async def handle_wake_word(self, event):
        """Fires when the user says the wake word (e.g., 'Alexa')."""
        trigger = event.data.get("trigger")
        score = event.data.get("score", 0)
        self.logger.info(f"\n🔔 Orchestrator: System woken up by '{trigger}' (Score: {score:.2f})!")

        # 🟢 CAPTURE COMMAND: Use the pipeline to listen for the actual request
        loop = asyncio.get_running_loop()
        
        # We use pipeline.capture_command() here as it handles VAD and STT transcription
        command_text = await loop.run_in_executor(None, self.pipeline.capture_command)
        
        if command_text:
            self.logger.info(f"🎤 Captured Command: '{command_text}'")
            # This triggers handle_user_input and kicks off Phase 1
            await bus.emit("user_input_received", {"text": command_text}, source="orchestrator")
        else:
            self.logger.warning("🔇 Orchestrator: No voice command understood after wake word.")

    async def handle_user_input(self, event):
        """
        Standard handler for processing any text command.
        This is the bridge between a raw string and the AI execution pipeline.
        """
        # Route the input to the task initialization logic
        await self.handle_new_task(event)

    async def handle_new_task(self, event):
        """Phase 1: Initialization & Context Gathering."""
        task_id = str(uuid.uuid4())[:8]
        user_text = event.data.get("text")
        
        if not user_text:
            return

        self.active_tasks[task_id] = {
            "status": "gathering_context",
            "input": user_text,
            "context": {}
        }

        self.logger.info(f"🎛️ Task [{task_id}] Initialized: '{user_text}'")

        # 1. Context Snapshot Request (What window is open? What time is it?)
        await bus.emit("request_context_snapshot", {"task_id": task_id}, source="orchestrator")

        # 2. Intent Parsing Request (What does the user want to do?)
        await bus.emit("request_intent_parsing", {
            "task_id": task_id,
            "text": user_text
        }, source="orchestrator")

    async def route_to_mapper(self, event):
        """Phase 2: Intent -> Capability Mapping."""
        await bus.emit("request_capability_mapping", event.data, source="orchestrator")

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