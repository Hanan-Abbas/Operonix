import asyncio
import logging
import os  # 🟢 Added to prevent NameError on hard exit!
import signal
import sys
from datetime import datetime
from api.server import start_server
from brain.capability_mapper import capability_mapper
from brain.llm_client import llm_client
from brain.planner import planner
from brain.decision_engine import decision_engine
from capabilities.bootstrap import init_capabilities
from context.state_extractor import state_extractor
from context.window_detector import window_detector
from core.config import settings
from core.error_handler import ErrorHandler
from core.event_bus import bus
from memory.session_memory import session_memory
from core.logger import sys_logger
from core.orchestrator import orchestrator
from debugging.error_listener import error_listener
from executor.executor import executor
from memory.long_term_memory import long_term_memory
from memory.vector_store import vector_store
from safety.confirmation import confirmation_manager
from learning.learner import learner
from learning.pruning import pattern_pruner

# Instantiating standard Python logger for console reporting
logger = logging.getLogger("LifecycleManager")

class LifecycleManager:
    """Manages the startup, execution hooks, dashboard API, and graceful
    shutdown of the AI OS.
    """

    def __init__(self):
        self.is_running = False
        self._background_tasks = set()
        
        self.error_handler = ErrorHandler(event_bus=bus, logger=sys_logger)

    def setup_global_exception_hooks(self, loop):
        """Binds the error handler to the OS and Async event loop."""

        def handle_sync_exception(exctype, value, traceback):
            if exctype is KeyboardInterrupt:
                sys.__excepthook__(exctype, value, traceback)
                return
            self.error_handler.handle_error(value, component="sys_level")

        sys.excepthook = handle_sync_exception

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
        """Initializes and boots all core system components in the correct order."""
        logger.info("🚀 Operonix Agent: Starting engine...")
        self.is_running = True

        loop = asyncio.get_running_loop()
        self.setup_global_exception_hooks(loop)

        # 1. First establish foundational capabilities
        init_capabilities()

        # 2. Boot the Highway & the Logger listening to it
        bus_task = asyncio.create_task(bus.run())
        self._background_tasks.add(bus_task)
        bus_task.add_done_callback(self._background_tasks.discard)
        await error_listener.start()
        
        await sys_logger.start()

        # 3. Boot LLM first before any system that relies on it!
        await llm_client.start()
        
        # 4. Boot remaining brain components
        await capability_mapper.start()
        await decision_engine.start()  
        await planner.start()
        
        # 5. Boot context & execution layers
        await window_detector.start()
        await state_extractor.start()
        await executor.start()
        
        # 6. Boot memory & management layers
        await session_memory.start()
        await long_term_memory.start()
        await vector_store.start()
        await confirmation_manager.start()
        await orchestrator.start()

        # 7. Boot the Learning System
        try:
            await learner.start()
            logger.info("🧠 Pattern Learner: Hooked to Event Bus.")
        except Exception as e:
            logger.error(f"Failed to start learning system: {e}")

        logger.info(
            "✨ All modules are synchronized and listening to the Event Bus."
        )

        self._register_signal_handlers(loop)

        bus.publish(
            "system_booting",
            {"timestamp": datetime.now().isoformat()},
            source="lifecycle",
        )

    def _register_signal_handlers(self, loop):
        """Captures OS-level termination signals to trigger a clean exit."""
        
        # Brutal sync handler for repeated interrupts
        def force_exit_handler():
            print("\n🛑 Force quit requested. Terminating immediately.")
            os._exit(1)

        for sig in (signal.SIGINT, signal.SIGTERM):
            try:
                # If we are already shutting down and the user hits Ctrl+C again,
                # immediately terminate without waiting.
                loop.add_signal_handler(
                    sig, 
                    lambda: asyncio.create_task(self.shutdown()) if self.is_running else force_exit_handler()
                )
            except NotImplementedError:
                pass

    async def run_forever(self):
        """Keeps the main thread alive and runs the FastAPI server."""
        try:
            await self.startup()

            logger.info("🌐 Dashboard API: Launching on http://localhost:8000")

            server_task = asyncio.create_task(asyncio.to_thread(start_server))
            self._background_tasks.add(server_task)
            server_task.add_done_callback(self._background_tasks.discard)

            while self.is_running:
                await asyncio.sleep(1)

        except Exception as e:
            try:
                self.error_handler.handle_error(e, component="main_core")
            except Exception:
                pass
            logger.critical(f"💥 Critical System Failure: {e}")
        finally:
            await self.shutdown()

    async def shutdown(self):
        """Performs an orderly cleanup of memory, active plugins, and background tasks."""
        if not self.is_running:
            return

        logger.info("🛑 Shutdown signal received. Powering down safely...")
        self.is_running = False

        bus.publish(
            "system_shutting_down",
            {"status": "saving_memory"},
            source="lifecycle",
        )

        # 1. Force the learner to save patterns to disk before tasks die!
        try:
            learner._save_store()
            logger.info("💾 Flushed learned patterns to pattern_store.json")
        except Exception as e:
            logger.error(f"Failed to save patterns on shutdown: {e}")

        # 2. Run the pruner with a strict async timeout so it can't hang the loop!
        try:
            logger.info("✂️ Running memory optimizer...")
            await asyncio.wait_for(pattern_pruner.prune_store(), timeout=2.0)
            logger.info("✂️ Memory optimized successfully.")
        except asyncio.TimeoutError:
            logger.warning("⏰ Pattern pruner took too long. Skipping optimization to avoid hanging.")
        except Exception as e:
            logger.error(f"Failed to prune pattern store: {e}")

        await asyncio.sleep(0.5)

        # 3. Now safely cancel all running tasks
        tasks = [
            t for t in asyncio.all_tasks() if t is not asyncio.current_task()
        ]
        logger.info(f"Cancelling {len(tasks)} remaining active tasks...")
        for task in tasks:
            task.cancel()

        try:
            await asyncio.wait_for(
                asyncio.gather(*tasks, return_exceptions=True), 
                timeout=2.0
            )
        except (asyncio.TimeoutError, asyncio.CancelledError):
            logger.warning("⏰ Tasks refused to exit. Hard killing the process.")

        # The Nuclear Option
        logger.info("🔌 System shut down completed. Goodbye.")
        os._exit(0)


# Global instance
lifecycle_manager = LifecycleManager()