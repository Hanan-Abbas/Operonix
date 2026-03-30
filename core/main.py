import asyncio
import sys

from api.server import start_server
from brain.capability_mapper import capability_mapper
from brain.llm_client import llm_client
from brain.planner import planner
from capabilities.bootstrap import init_capabilities
from context.state_extractor import state_extractor
from context.window_detector import window_detector
from core.event_bus import bus
from core.logger import logger
# --- IMPORT ERROR HANDLER ---
from core.error_handler import ErrorHandler
from core.orchestrator import orchestrator
from executor.executor import executor
from learning.evolution_engine import evolution_engine

class IOSAgent:
    def __init__(self):
        self.is_running = True
        # Initialize the error handler with your global bus and logger
        self.error_handler = ErrorHandler(event_bus=bus, logger=logger)

    def setup_global_exception_hooks(self, loop):
        """Binds the error handler to the OS and Async event loop."""
        # 1. Catch standard synchronous crashes
        def handle_sync_exception(exctype, value, traceback):
            if exctype is KeyboardInterrupt:
                sys.__excepthook__(exctype, value, traceback)
                return
            self.error_handler.handle_error(value, component="sys_level")
        
        sys.excepthook = handle_sync_exception

        # 2. Catch silent background async crashes
        def handle_async_exception(loop, context):
            exception = context.get('exception')
            if exception:
                self.error_handler.handle_error(exception, component="async_loop")
            else:
                logger.warning(f"Unhandled task error: {context['message']}")

        loop.set_exception_handler(handle_async_exception)

    async def initialize_modules(self):
        print("🚀 Operonix Agent: Starting engine...")

        init_capabilities()

        # Order matters! Logger first to catch errors.
        await logger.start()
        await evolution_engine.start()
        await capability_mapper.start()
        await executor.start()
        await window_detector.start()
        await state_extractor.start()
        await llm_client.start()
        await planner.start()
        await orchestrator.start()

        print("✨ All modules are synchronized and listening to the Event Bus.")

    async def run_forever(self):
        """Keep the agent alive and start the dashboard server."""
        try:
            # Get the current loop and attach our error handlers
            loop = asyncio.get_event_loop()
            self.setup_global_exception_hooks(loop)

            await self.initialize_modules()

            print("🌐 Dashboard API: Launching on http://localhost:8000")
            
            # This launches the FastAPI server without blocking the event loop
            await loop.run_in_executor(None, start_server)

            # Keep the main thread alive so background tasks continue
            while self.is_running:
                await asyncio.sleep(1)

        except Exception as e:
            # This catches anything that escapes during startup
            self.error_handler.handle_error(e, component="main_core")
            print(f"💥 Critical System Failure: {e}")
        finally:
            self.cleanup()

    def cleanup(self):
        print("🔌 Powering down modules safely.")
        sys.exit(0)

if __name__ == "__main__":
    agent = IOSAgent()
    try:
        asyncio.run(agent.run_forever())
    except KeyboardInterrupt:
        print("\n👋 Operonix Agent: Offline. Goodbye.")