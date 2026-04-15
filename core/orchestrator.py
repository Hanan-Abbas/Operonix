"""
core/orchestrator.py — Operonix AI OS Agent
═════════════════════════════════════════════
Top-level coordinator.  Creates the AudioManager once and shares it
with WakeWordDetector and VoicePipeline.

Fixes vs original:
  • wake_detector.loop assigned BEFORE create_task (eliminates race)
  • VoiceListener kept as optional fallback, not instantiated by default
    (pipeline.capture_command is more robust)
  • handle_user_input no longer double-routes through handle_new_task
    (was creating two tasks for every input)
  • Graceful shutdown via stop()
"""
from __future__ import annotations

import asyncio
import logging
import uuid
from typing import Optional

from core.config import settings
from core.event_bus import bus
from voice.audio_manager import AudioManager
from voice.wake_word import WakeWordDetector
from voice.pipeline import VoicePipeline

logger = logging.getLogger("Orchestrator")


class Orchestrator:
    def __init__(self) -> None:
        self.active_tasks: dict = {}
        self.is_running: bool = False

        # ── Single AudioManager — shared by all voice subsystems ──────────────
        self.audio_manager = AudioManager(
            rate=int(getattr(settings, "AUDIO_RATE", 16000)),
            chunk=int(getattr(settings, "AUDIO_CHUNK", 1280)),
            auto_start=True,
        )

        self.pipeline = VoicePipeline(audio_manager=self.audio_manager)

        wake_phrase = getattr(settings, "WAKE_WORD", "alexa")
        self.wake_detector = WakeWordDetector(
            wake_word=wake_phrase,
            audio_manager=self.audio_manager,
        )

        # VoiceListener is optional — used only for simple CLI tests
        self._listener: Optional[object] = None

    # ── Lifecycle ─────────────────────────────────────────────────────────────

    async def start(self) -> None:
        self.is_running = True

        # IMPORTANT: assign the loop BEFORE creating any tasks that depend on it
        loop = asyncio.get_running_loop()
        self.wake_detector.loop = loop

        bus.subscribe("wake_word_detected", self.handle_wake_word)
        bus.subscribe("user_input_received", self.handle_new_task)

        asyncio.create_task(self._background_wake_loop())
        logger.info("👂 Orchestrator: wake-word detection started (%r).", self.wake_detector.wake_word)

    async def stop(self) -> None:
        self.is_running = False
        self.audio_manager.stop()
        logger.info("🛑 Orchestrator stopped.")

    # ── Background wake-word loop ─────────────────────────────────────────────

    async def _background_wake_loop(self) -> None:
        loop = asyncio.get_running_loop()
        while self.is_running:
            await loop.run_in_executor(None, self.wake_detector.detect)
            await asyncio.sleep(0.005)   # yield to event loop without busy-spinning

    # ── Wake-word handler ─────────────────────────────────────────────────────

    async def handle_wake_word(self, event) -> None:
        trigger = event.data.get("trigger", "?")
        score = event.data.get("score", 0.0)
        logger.info("🔔 Wake word detected (score=%.2f) — capturing command", score)

        loop = asyncio.get_running_loop()
        
        try:
            command = await loop.run_in_executor(
                None, 
                self.pipeline.capture_command
            )

            if command and command.get("text"):
                text = command["text"]
                confidence = command.get("confidence", 0.0)
                
                logger.info("🎤 Command (conf=%.2f): '%s'", confidence, text)
                
                await bus.emit(
                    "user_input_received",
                    {
                        "text": text,
                        "stt": command.get("stt", {}),
                        "stt_provider": command.get("provider"),
                        "confidence": confidence,
                        "duration": command.get("duration_seconds", 0)
                    },
                    source="orchestrator",
                )
            else:
                logger.info("🔇 Command capture returned None (likely silence)")
                
        except Exception as e:
            logger.error("❌ Voice capture failed: %s", e)
            await bus.emit(
                "voice_capture_error",
                {"error": str(e)},
                source="orchestrator"
            )

    # ── Task lifecycle ────────────────────────────────────────────────────────

    async def handle_new_task(self, event) -> None:
        """Phase 1: Initialise task and fan out to context + intent services."""
        task_id = str(uuid.uuid4())[:8]
        user_text = event.data.get("text", "").strip()

        if not user_text:
            return

        self.active_tasks[task_id] = {
            "status": "gathering_context",
            "input": user_text,
            "context": {},
        }
        logger.info("🎛️  Task [%s] initialised: %r", task_id, user_text)

        await bus.emit("request_context_snapshot", {"task_id": task_id}, source="orchestrator")
        await bus.emit(
            "request_intent_parsing",
            {
                "task_id": task_id,
                "text": user_text,
                "stt": event.data.get("stt") or {},
                "stt_provider": event.data.get("stt_provider"),
            },
            source="orchestrator",
        )

    async def route_to_mapper(self, event) -> None:
        await bus.emit("request_capability_mapping", event.data, source="orchestrator")

    async def route_to_executor(self, event) -> None:
        await bus.emit("request_execution", event.data, source="orchestrator")

    async def handle_failure(self, event) -> None:
        task_id = event.data.get("task_id")
        error = event.data.get("error")
        logger.error("❌ Task [%s] failed: %s", task_id, error)
        await bus.emit(
            "error_detected",
            {"task_id": task_id, "error": error,
             "context": self.active_tasks.get(task_id, {}).get("context")},
            source="orchestrator",
        )

    async def finalize_task(self, event) -> None:
        task_id = event.data.get("task_id")
        logger.info("✅ Task [%s] completed.", task_id)
        self.active_tasks.pop(task_id, None)


# ── Singleton ─────────────────────────────────────────────────────────────────
orchestrator = Orchestrator()


# ── Standalone entry point ────────────────────────────────────────────────────
if __name__ == "__main__":
    import logging
    logging.basicConfig(level=logging.INFO)

    async def _main():
        await orchestrator.start()
        try:
            while True:
                await asyncio.sleep(1)
        except asyncio.CancelledError:
            pass
        finally:
            await orchestrator.stop()

    try:
        asyncio.run(_main())
    except KeyboardInterrupt:
        print("\n🛑 Orchestrator stopped.")