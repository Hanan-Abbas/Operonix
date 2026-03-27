import asyncio
import signal
import sys
from core.orchestrator import orchestrator
from core.event_bus import bus
from brain.llm_client import llm_client
from brain.planner import planner
from brain.capability_mapper import capability_mapper
from context.window_detector import window_detector
from context.app_classifier import app_classifier
from context.state_extractor import state_extractor
from executor.executor import executor
from tools.tool_registry import tool_registry
from api.server import start_server

class IOSAgent:
    def __init__(self):
        self.is_running = True

    async def initialize_modules(self):
        """
        Wakes up every module in the 17-folder structure.
        The order of 'start()' calls matters for event subscriptions.
        """
        print("🚀 i_os Agent: Starting engine...")

        # 1. Start the Brain & Logic
        await llm_client.start()
        await capability_mapper.start()
        await planner.start()

        # 2. Start the Context Awareness
        await window_detector.start()
        await app_classifier.start()
        await state_extractor.start()

        # 3. Start the Execution Layer
        await executor.start()

        # 4. Start the Heart (Orchestrator)
        await orchestrator.start()

        print("✨ All modules are synchronized and listening to the Event Bus.")

    async def run_forever(self):
        """
        Main loop to keep the background processes alive while 
        the API server handles the dashboard.
        """
        try:
            await self.initialize_modules()

            print("🌐 Dashboard API: Launching on http://localhost:8000")
            
            # Start the server in a separate thread so it doesn't block the Event Bus
            loop = asyncio.get_event_loop()
            await loop.run_in_executor(None, start_server)

        except asyncio.CancelledError:
            print("🛑 System shutdown initiated...")
        except Exception as e:
            print(f"💥 Critical System Failure: {e}")
        finally:
            self.cleanup()

    def cleanup(self):
        """Graceful shutdown logic."""
        print("🔌 Powering down modules safely.")
        sys.exit(0)

if __name__ == "__main__":
    # The entry point of your entire OS Agent
    agent = IOSAgent()
    
    try:
        asyncio.run(agent.run_forever())
    except KeyboardInterrupt:
        print("\n👋 i_os Agent: Offline. Goodbye.")