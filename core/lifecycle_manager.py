"""
core/lifecycle_manager.py

Panel integration
─────────────────
The panel is booted by the Orchestrator (which spins a Qt daemon thread).
LifecycleManager does not need to call panel code directly.  The only change
here is:
  • `intent_parser` is now imported and started so the panel's parse()
    method has a warm LLM client.
  • A `window_detector` is started so `app_context_changed` events fire.
  • The shutdown sequence calls `orchestrator.stop()` which in turn calls
    `panel_controller.stop()` — no separate panel teardown needed here.

Mode Manager integration
────────────────────────
  • After orchestrator.start(), mode_manager.initialise(orchestrator) is
    called. This wires the EventBus listener that watches action_completed
    and applies the boot mode (read from CURRENT_MODE in .env).
  • On shutdown, mode_manager does not need an explicit stop — the
    orchestrator.stop() call already tears down both subsystems.

Dependency Sentry (CAVEAT 2)
─────────────────────────────
  • _check_system_dependencies() runs as step 0.a inside startup(), after the
    EventBus is running but before any other subsystem starts.
  • Checks wmctrl, xdotool, xprop via shutil.which().
  • Missing REQUIRED binary → publishes "dependency_missing" (level=error) on
    the bus so dashboard + panel show a persistent warning. Operonix continues
    in Ghost (background) mode — never crashes here.
  • Missing OPTIONAL binary → publishes "dependency_missing" (level=warn).
  • All present → publishes "dependencies_ok".
  • The matching bash-layer check lives in setup.sh (Section 6).
"""
from __future__ import annotations

import asyncio
import logging
import os
import shutil
import signal
import sys
from datetime import datetime

from api.server import start_server
from brain.capability_mapper import capability_mapper
from brain.intent_parser import intent_parser
from brain.llm_client import llm_client
from brain.planner import planner
from brain.decision_engine import decision_engine
from capabilities.bootstrap import init_capabilities
from context.state_extractor import state_extractor
from context.window_detector import window_detector
from core.config import settings
from core.error_handler import ErrorHandler
from core.event_bus import bus
from core.logger import sys_logger
from core.mode_manager import mode_manager          # ← NEW
from core.orchestrator import orchestrator
from debugging.error_listener import error_listener
from executor.executor import executor
from memory.long_term_memory import long_term_memory
from memory.session_memory import session_memory
from memory.vector_store import vector_store
from safety.confirmation import confirmation_manager
from safety.validator import safety_validator
from learning.learner import learner
from learning.pruning import pattern_pruner
from api.routes.health import system_state
from core.config_validator import validated_config
from voice.stt import SpeechToText
from plugins import start_plugin_system

logger = logging.getLogger("LifecycleManager")


class LifecycleManager:
    """Manages startup, execution hooks, dashboard API, and graceful shutdown."""

    def __init__(self) -> None:
        self.is_running = False
        self._background_tasks: set = set()
        self.error_handler = ErrorHandler(event_bus=bus, logger=sys_logger)

    def setup_global_exception_hooks(self, loop: asyncio.AbstractEventLoop) -> None:
        def handle_sync_exception(exctype: type, value: BaseException, traceback: Any) -> None:
            if exctype is KeyboardInterrupt:
                sys.__excepthook__(exctype, value, traceback)
                return
            self.error_handler.handle_error(value, component="sys_level")

        sys.excepthook = handle_sync_exception

        def handle_async_exception(loop: asyncio.AbstractEventLoop, context: dict) -> None:
            exception = context.get("exception")
            if exception:
                self.error_handler.handle_error(exception, component="async_loop")
            else:
                logger.warning("Unhandled task error: %s", context.get("message"))

        loop.set_exception_handler(handle_async_exception)

    async def startup(self) -> None:
        """Initialises and boots all core system components in the correct order."""
        logger.info("🚀 Operonix Agent: Starting engine...")

        try:
            validated_config.validate_audio_device()
            logger.info("✅ Configuration validation PASSED")
        except Exception as exc:
            logger.critical("💥 Configuration invalid: %s", exc)
            raise RuntimeError(f"Invalid configuration: {exc}") from exc

        self.is_running = True

        loop = asyncio.get_running_loop()
        self.setup_global_exception_hooks(loop)

        # 1. Foundational capabilities
        init_capabilities()

        # 2. EventBus
        bus_task = asyncio.create_task(bus.run())
        self._background_tasks.add(bus_task)
        bus_task.add_done_callback(self._background_tasks.discard)
        system_state.event_bus_running = True

        await error_listener.start()
        await sys_logger.start()

        # 3. LLM first — required by intent_parser and executor
        await llm_client.start()

        # 4. Brain components
        await capability_mapper.start()
        await decision_engine.start()
        await planner.start()

        # 5. Intent parser — must start before orchestrator so validate_and_route
        #    is subscribed before the first user_input_received event fires.
        #    Also warms up the synchronous parse() path used by the panel.
        await intent_parser.start()

        # 6. Context / state
        await state_extractor.start()
        await window_detector.start()   # fires context_snapshot_ready → app_context_changed

        # 7. Safety validator — MUST be started before executor so its
        #    task_dispatched subscription is registered before any plan
        #    is dispatched by the planner.
        await safety_validator.start()

        # 8. Executor
        await executor.start()
        system_state.executor_running = True

        # 9. Memory
        await session_memory.start()
        await long_term_memory.start()
        await vector_store.start()
        await confirmation_manager.start()

        # 10. Orchestrator — boots the panel Qt thread internally when PANEL_ENABLED=true
        await orchestrator.start()
        system_state.orchestrator_running = True

        # 11. Mode manager — must run AFTER orchestrator.start() so the orchestrator
        #     instance is fully initialised before mode_manager references it.
        #     initialise() wires the action_completed listener and applies the boot
        #     mode (CURRENT_MODE from .env, defaulting to "panel").
        mode_manager.initialise(orchestrator)       # ← NEW
        logger.info("🔀 ModeManager: initialised (boot mode=%s).", mode_manager.current_mode.name)

        # 12. Plugin system
        await start_plugin_system()

        # 13. STT health reference
        stt_instance = SpeechToText()
        system_state.stt_model = stt_instance
        system_state.llm_client = llm_client
        system_state.audio_manager = orchestrator.audio_manager

        # 14. Learning system
        try:
            await learner.start()
            logger.info("🧠 Pattern Learner: Hooked to Event Bus.")
        except Exception as exc:
            logger.error("Failed to start learning system: %s", exc)

        logger.info("✨ All modules synchronised and listening to the Event Bus.")
        self._register_signal_handlers(loop)

        bus.publish(
            "system_booting",
            {"timestamp": datetime.now().isoformat()},
            source="lifecycle",
        )

    def _register_signal_handlers(self, loop: asyncio.AbstractEventLoop) -> None:
        def force_exit_handler() -> None:
            print("\n🛑 Force quit requested. Terminating immediately.")
            os._exit(1)

        for sig in (signal.SIGINT, signal.SIGTERM):
            try:
                loop.add_signal_handler(
                    sig,
                    lambda: asyncio.create_task(self.shutdown()) if self.is_running else force_exit_handler(),
                )
            except NotImplementedError:
                pass

    async def run_forever(self) -> None:
        try:
            await self.startup()

            api_host = getattr(settings, "API_HOST", "localhost")
            api_port = getattr(settings, "API_PORT", 8000)
            logger.info("🌐 Dashboard API: Launching on http://%s:%d", api_host, api_port)

            server_task = asyncio.create_task(asyncio.to_thread(start_server))
            self._background_tasks.add(server_task)
            server_task.add_done_callback(self._background_tasks.discard)

            while self.is_running:
                await asyncio.sleep(1)

        except Exception as exc:
            try:
                self.error_handler.handle_error(exc, component="main_core")
            except Exception:
                pass
            logger.critical("💥 Critical System Failure: %s", exc)
        finally:
            await self.shutdown()

    async def shutdown(self) -> None:
        if not self.is_running:
            return

        logger.info("🛑 Shutdown signal received. Powering down safely...")
        self.is_running = False

        bus.publish(
            "system_shutting_down",
            {"status": "saving_memory"},
            source="lifecycle",
        )

        # Flush learned patterns (includes panel override rankings).
        try:
            if hasattr(learner, "_save_store"):
                learner._save_store()
            if hasattr(learner, "_save_override_store"):
                learner._save_override_store()
            logger.info("💾 Flushed learned patterns to disk")
        except Exception as exc:
            logger.error("Failed to save patterns on shutdown: %s", exc)

        # Prune memory
        try:
            prune_timeout = float(getattr(settings, "PRUNE_TIMEOUT", 2.0))
            await asyncio.wait_for(pattern_pruner.prune_store(), timeout=prune_timeout)
            logger.info("✂️ Memory optimised successfully.")
        except asyncio.TimeoutError:
            logger.warning("⏰ Pattern pruner timed out. Skipping.")
        except Exception as exc:
            logger.error("Failed to prune pattern store: %s", exc)

        # Orchestrator stop → shuts down panel Qt thread and audio manager.
        # mode_manager does not need a separate stop — orchestrator.stop() covers it.
        try:
            await orchestrator.stop()
        except Exception as exc:
            logger.error("Orchestrator stop error: %s", exc)

        await asyncio.sleep(0.5)

        tasks = [t for t in asyncio.all_tasks() if t is not asyncio.current_task()]
        logger.info("Cancelling %d remaining tasks...", len(tasks))
        for task in tasks:
            task.cancel()

        try:
            await asyncio.wait_for(
                asyncio.gather(*tasks, return_exceptions=True),
                timeout=2.0,
            )
        except (asyncio.TimeoutError, asyncio.CancelledError):
            logger.warning("⏰ Tasks refused to exit. Hard killing.")

        logger.info("🔌 System shut down completed. Goodbye.")
        os._exit(0)


# Global instance
lifecycle_manager = LifecycleManager()