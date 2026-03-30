import asyncio
import logging
import signal
import sys
from core.config import settings
from core.event_bus import bus

logger = logging.getLogger("LifecycleManager")


class LifecycleManager:
    """Manages the startup, graceful shutdown, and emergency recovery of the AI

    OS.
    """

    def __init__(self):
        self.is_running = False
        self._background_tasks = set()

    async def startup(self):
        """Initializes and boots all core system components in the correct

        order.
        """
        logger.info("System is initializing...")
        self.is_running = True

        # 1. Start the Event Bus first so everything else can communicate
        bus_task = asyncio.create_task(bus.run())
        self._background_tasks.add(bus_task)
        bus_task.add_done_callback(self._background_tasks.discard)

        # 2. Register OS Signal Handlers for safe cancellation (CTRL+C)
        self._register_signal_handlers()

        # 3. Fire system_started event
        bus.publish(
            "system_booting",
            {"timestamp": settings.BASE_DIR.name},
            source="lifecycle",
        )

        logger.info("🤖 Operonix AI OS successfully booted.")

    def _register_signal_handlers(self):
        """Captures OS-level termination signals to trigger a clean exit."""
        for sig in (signal.SIGINT, signal.SIGTERM):
            try:
                loop = asyncio.get_running_loop()
                loop.add_signal_handler(
                    sig, lambda: asyncio.create_task(self.shutdown())
                )
            except NotImplementedError:
                # Windows does not support add_signal_handler in asyncio
                pass

    async def shutdown(self):
        """Performs an orderly cleanup of memory, active plugins, and background

        tasks.
        """
        if not self.is_running:
            return

        logger.info("🛑 Shutdown signal received. Powering down safely...")
        self.is_running = False

        # 1. Alert the system we are shutting down
        bus.publish(
            "system_shutting_down",
            {"status": "saving_memory"},
            source="lifecycle",
        )

        # 2. Give background tasks a few seconds to finish saving files/logs
        await asyncio.sleep(1)

        # 3. Cancel all running tasks
        tasks = [
            t for t in asyncio.all_tasks() if t is not asyncio.current_task()
        ]
        logger.info(f"Cancelling {len(tasks)} remaining active tasks...")
        for task in tasks:
            task.cancel()

        # Wait for cancellation to complete
        await asyncio.gather(*tasks, return_exceptions=True)

        logger.info("🔌 System shut down completed. Goodbye.")
        sys.exit(0)

    async def recover_component(self, component_name: str):
        """If a background module crashes (like the executor or voice listener),

        this attempts to reboot just that slice without taking down the whole

        OS.
        """
        logger.warning(
            f"🛠️ Attempting self-healing recovery for: {component_name}"
        )
        bus.publish(
            "component_recovering",
            {"component": component_name},
            source="lifecycle",
        )

        # Add your logic here to re-instantiate or restart specific workers
        # e.g., if component_name == 'executor': await executor.start()

        await asyncio.sleep(0.5)
        logger.info(f"✅ Component {component_name} recovery sequence executed.")


# Global instance
lifecycle_manager = LifecycleManager()