import asyncio
import signal
import sys
from core.logger import logger 
from core.event_bus import bus
from core.orchestrator import orchestrator
from brain.llm_client import llm_client
from brain.planner import planner
from brain.capability_mapper import capability_mapper
from context.window_detector import window_detector
from context.app_classifier import classifier 
from context.state_extractor import state_extractor
from executor.executor import executor
from api.server import start_server

class IOSAgent:
    def __init__(self):
        self.is_running = True

    async def initialize_modules(self):
        print("🚀 Operonix Agent: Starting engine...")

        # Start this FIRST so it can record the startup success/failure of everything else.
        await logger.start()

        # We want the system to be able to DO things before it starts THINKING.
        await capability_mapper.start()
        await executor.start()

        # Note: We don't 'start' the classifier usually, we just use it.
        await window_detector.start()
        await state_extractor.start()

        # Now that the eyes and hands are ready, we wake up the intellect.
        await llm_client.start()
        await planner.start()

        # The Boss wakes up last. It now has a logger, hands, eyes, and a brain ready to go.
        await orchestrator.start()

        print("✨ All modules are synchronized and listening to the Event Bus.")