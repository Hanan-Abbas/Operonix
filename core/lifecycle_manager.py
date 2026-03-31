import asyncio
import logging
import signal
import sys
from api.server import start_server
from brain.capability_mapper import capability_mapper
from brain.llm_client import llm_client
from brain.planner import planner
from capabilities.bootstrap import init_capabilities
from context.state_extractor import state_extractor
from context.window_detector import window_detector
from core.config import settings
from core.error_handler import ErrorHandler
from core.event_bus import bus
from memory.session_memory import session_memory
from core.logger import logger
from core.orchestrator import orchestrator
from debugging.error_listener import error_listener
from executor.executor import executor
from learning.evolution_engine import evolution_engine
from memory.long_term_memory import long_term_memory
from memory.vector_store import vector_store
from safety.confirmation import ConfirmationManager

class LifecycleManager:
    """Manages the startup, execution hooks, dashboard API, and graceful

    shutdown of the AI OS.
    """

    def __init__(self):
        self.is_running = False
        self._background_tasks = set()
        # Initialize the global error handler here
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
            exception = context.get("exception")
            if exception:
                self.error_handler.handle_error(
                    exception, component="async_loop"
                )
            else:
                logger.warning(
                    f"Unhandled task error: {context.get('message')}"
                )

        loop.set_exception_handler(handle_async_exception)

    async def startup(self):
        """Initializes and boots all core system components in the correct

        order.
        """
        logger.info("🚀 Operonix Agent: Starting engine...")
        self.is_running = True

        # Attach the hooks to the running loop
        loop = asyncio.get_running_loop()
        self.setup_global_exception_hooks(loop)

        # Initialize capabilities first
        init_capabilities()

        # 1. Fire up the Event Bus in the background!
        bus_task = asyncio.create_task(bus.run())
        self._background_tasks.add(bus_task)
        bus_task.add_done_callback(self._background_tasks.discard)

        # 2. Fire up the Error Listener!
        await error_listener.start()

        # 3. Boot remaining modules in correct chain of command
        await logger.start()
        await evolution_engine.start()
        await capability_mapper.start()
        await executor.start()
        await window_detector.start()
        await state_extractor.start()
        await llm_client.start()
        await planner.start()
        await orchestrator.start()
        await session_memory.start()
        await long_term_memory.start()
        await vector_store.start()
        await confirmation_manager.start()


        logger.info(
            "✨ All modules are synchronized and listening to the Event Bus."
        )

        # 4. Register OS Signal Handlers for safe cancellation (CTRL+C)
        self._register_signal_handlers(loop)

        # 5. Broadcast bootup
        bus.publish(
            "system_booting",
            {"timestamp": settings.BASE_DIR.name},
            source="lifecycle",
        )

    def _register_signal_handlers(self, loop):
        """Captures OS-level termination signals to trigger a clean exit."""
        for sig in (signal.SIGINT, signal.SIGTERM):
            try:
                loop.add_signal_handler(
                    sig, lambda: asyncio.create_task(self.shutdown())
                )
            except NotImplementedError:
                # Windows doesn't support add_signal_handler in asyncio
                pass

    async def run_forever(self):
        """Keeps the main thread alive and runs the FastAPI server."""
        try:
            await self.startup()

            logger.info("🌐 Dashboard API: Launching on http://localhost:8000")

            # This launches the FastAPI server without blocking the event loop
            loop = asyncio.get_running_loop()
            await loop.run_in_executor(None, start_server)

            # Keep the main thread alive so background tasks continue
            while self.is_running:
                await asyncio.sleep(1)

        except Exception as e:
            self.error_handler.handle_error(e, component="main_core")
            logger.critical(f"💥 Critical System Failure: {e}")
        finally:
            await self.shutdown()

    async def shutdown(self):
        """Performs an orderly cleanup of memory, active plugins, and background

        tasks.
        """
        if not self.is_running:
            return

        logger.info("🛑 Shutdown signal received. Powering down safely...")
        self.is_running = False

        bus.publish(
            "system_shutting_down",
            {"status": "saving_memory"},
            source="lifecycle",
        )

        await asyncio.sleep(1)

        # Cancel all running tasks except current
        tasks = [
            t for t in asyncio.all_tasks() if t is not asyncio.current_task()
        ]
        logger.info(f"Cancelling {len(tasks)} remaining active tasks...")
        for task in tasks:
            task.cancel()

        await asyncio.gather(*tasks, return_exceptions=True)

        logger.info("🔌 System shut down completed. Goodbye.")
        sys.exit(0)


# Global instance
lifecycle_manager = LifecycleManager()