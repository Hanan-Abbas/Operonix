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

REFLECTOR INTEGRATION
─────────────────────
Startup order changes (all guarded with try/except for degraded-mode safety):

  Step 9a  — episodic_memory.start() inserted BEFORE orchestrator.start().
              Required because orchestrator.start() boots the Reflector, which
              calls episodic_memory.store() immediately on first task. Starting
              episodic_memory after orchestrator would cause _conn=None errors.

  Step 12a — capability_gap_detector.start() + plugin_evolver.start() inserted
              AFTER start_plugin_system() so plugin_registry is fully populated
              before either subscriber fires. Both now subscribe to
              "evolution_needed" (from Reflector) in addition to their existing
              subscriptions.

  Bug fix  — `from typing import Any` added to imports. `Any` was used in
              setup_global_exception_hooks() type hints but was never imported,
              causing a NameError if the function signature was ever introspected
              (e.g. by the error_handler or test runner).

Shutdown changes:
  • Reflector final stats (reflections_total, evolution_triggers) are written
    to core.metrics.SystemMetrics before process exit so the dashboard API
    serves accurate lifetime counts even after a clean shutdown.
  • LongTermMemory._kv_conn (SQLite) is explicitly closed so WAL-mode journals
    are checkpointed before the process exits — prevents DB corruption on
    hard-restart.
"""
from __future__ import annotations

import asyncio
import logging
import os
import shutil
import signal
import sys
from datetime import datetime
from typing import Any                         # ← fixes NameError in setup_global_exception_hooks

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
from core.mode_manager import mode_manager
from core.orchestrator import orchestrator
from debugging.error_listener import error_listener
from executor.executor import executor
from memory.episodic import episodic_memory          # ← Reflector dependency
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

    # ── Dependency Sentry (CAVEAT 2) ──────────────────────────────────────────
    # Binary manifest: (name, role, required)
    #   required=True  → missing = error; Bridge/Lab disabled, Ghost fallback
    #   required=False → missing = warning; specific sub-feature degraded
    _SYSTEM_DEPS: list[tuple[str, str, bool]] = [
        ("wmctrl",         "Z-order terminal list — Bridge/Ghost routing",       True),
        ("xdotool",        "Active window focus-stack — Bridge routing",         True),
        ("xprop",          "WM_CLASS terminal-type detection",                   True),
        ("gnome-terminal", "Profile C Lab terminal (preferred)",                 False),
        ("xterm",          "Profile C Lab terminal (fallback)",                  False),
    ]

    async def _check_system_dependencies(self) -> None:
        """
        Dependency Sentry — runs once at startup after the EventBus is live.

        Uses shutil.which() to verify every binary in _SYSTEM_DEPS.
        Results are published on the bus so the dashboard live-log and panel
        status strip reflect the current state immediately.

        Operonix NEVER crashes here.  Missing required binaries cause
        terminal_resolver to fall back to Ghost automatically (it performs
        the same shutil.which() checks internally at resolve() time).
        """
        missing_required: list[str] = []
        missing_optional: list[str] = []
        present:          list[str] = []

        logger.info("🔍 Dependency Sentry: checking system binaries…")

        for binary, role, required in self._SYSTEM_DEPS:
            path = shutil.which(binary)
            if path:
                logger.info("  ✓ %-18s %s", binary, path)
                present.append(binary)
            else:
                if required:
                    logger.warning("  ✗ %-18s NOT FOUND (required) — %s", binary, role)
                    missing_required.append(binary)
                else:
                    logger.warning("  ~ %-18s not found  (optional) — %s", binary, role)
                    missing_optional.append(binary)

        # ── Broadcast results on the bus ──────────────────────────────────────
        if missing_required:
            bus.publish(
                "dependency_missing",
                {
                    "level":    "error",
                    "binaries": missing_required,
                    "message": (
                        f"Required system binaries missing: {', '.join(missing_required)}. "
                        f"Bridge and Lab profiles disabled — running in Ghost (background) mode. "
                        f"Fix: sudo apt install -y {' '.join(missing_required)}"
                    ),
                    "fallback": "ghost",
                    "fix_cmd":  f"sudo apt install -y {' '.join(missing_required)}",
                },
                source="lifecycle_manager",
            )

        if missing_optional:
            bus.publish(
                "dependency_missing",
                {
                    "level":    "warn",
                    "binaries": missing_optional,
                    "message": (
                        f"Optional binaries missing: {', '.join(missing_optional)}. "
                        f"Some features may be degraded. "
                        f"Fix: sudo apt install -y {' '.join(missing_optional)}"
                    ),
                    "fix_cmd": f"sudo apt install -y {' '.join(missing_optional)}",
                },
                source="lifecycle_manager",
            )

        if not missing_required and not missing_optional:
            bus.publish(
                "dependencies_ok",
                {
                    "binaries": present,
                    "message":  "All system dependencies present. Hybrid execution fully operational.",
                },
                source="lifecycle_manager",
            )
            logger.info("✅ Dependency Sentry: all binaries present — hybrid execution ready.")
        elif not missing_required:
            logger.info(
                "⚠️  Dependency Sentry: required binaries present; optional missing: %s",
                missing_optional,
            )
        else:
            logger.warning(
                "❌ Dependency Sentry: %d required binary(ies) missing — Ghost-only mode active.",
                len(missing_required),
            )

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

        # 2.a Dependency Sentry — runs after the EventBus is live so results
        #     are broadcast to the dashboard immediately.  Must run before any
        #     subsystem that depends on wmctrl/xdotool.  Never raises.
        #
        #     FIX: bus.run() is started as create_task() above. The coroutine
        #     sets bus._event_loop only when it actually begins executing.
        #     A single asyncio.sleep(0) is not always enough — the scheduler
        #     may not have dispatched bus.run() yet if other coroutines are
        #     queued ahead of it. We poll until bus._event_loop is populated
        #     (max 1 s / 20 × 50 ms) so bus.publish() inside the sentry never
        #     drops with "Event loop not initialized yet."
        for _ in range(20):
            await asyncio.sleep(0.05)
            if getattr(bus, "_event_loop", None) is not None:
                break
        await self._check_system_dependencies()

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

        # 9a. Episodic memory — MUST start before orchestrator so it is
        #     subscribed to task_completed / task_failed before the first
        #     task can fire. Also required by the Reflector (episodic.store())
        #     and CapabilityGapDetector (get_failures_in_window).
        #
        #     RISK: starting after long_term_memory ensures the stores/
        #     directory already exists (long_term_memory.start() calls
        #     os.makedirs on it), avoiding a race on the directory creation.
        try:
            await episodic_memory.start()
            logger.info("📖 Episodic Memory: started.")
        except Exception as exc:
            logger.error("Failed to start episodic memory: %s", exc)

        # 10. Orchestrator — boots the panel Qt thread internally when PANEL_ENABLED=true
        #     The Orchestrator's start() also boots the Reflector (brain.reflector)
        #     which subscribes to "execution_complete". Episodic memory must be
        #     running before this point (step 9a above).
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

        # 12a. Self-evolution subsystems — started AFTER the plugin system so
        #      plugin_registry is fully populated before these subscribers fire.
        #
        #      capability_gap_detector: subscribes to task_failed, mapping_failed,
        #        plugin_validation_failed, AND evolution_needed (from Reflector).
        #        Must be running before the first task so no gap events are missed.
        #
        #      plugin_evolver: subscribes to plugin_evolution_requested AND
        #        evolution_needed (from Reflector). Requires plugin_registry to be
        #        populated so _on_evolution_needed_reflector can look up installed
        #        plugins by intent.
        #
        #      RISK: both are started inside try/except so a failure here does not
        #        crash the system — Operonix continues without self-evolution
        #        capability and logs an error (degraded mode, same pattern as Reflector).
        try:
            from plugins.capability_gap_detector import capability_gap_detector
            from plugins.plugin_evolver import plugin_evolver
            await capability_gap_detector.start()
            await plugin_evolver.start()
            logger.info("🔎 CapabilityGapDetector + PluginEvolver: started.")
        except Exception as exc:
            logger.error(
                "Failed to start self-evolution subsystems (degraded mode): %s", exc
            )

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

        # Start Adaptive Trust Layer (interactive prompt pattern learning)
        try:
            from learning.prompt_trust import prompt_trust
            prompt_trust.start()
            logger.info("🎯 PromptTrustLayer: Online (threshold=%d approvals).", 5)
        except Exception as exc:
            logger.error("Failed to start PromptTrustLayer: %s", exc)

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

        # Flush Reflector stats to metrics and close KV store connection.
        # RISK: Reflector may not have been initialised (degraded mode boot).
        #   All access guarded with hasattr / try-except.
        try:
            from core.metrics import metrics
            reflector = getattr(orchestrator, "_reflector", None)
            if reflector is not None:
                stats = reflector.get_stats()
                metrics.reflections_total  = stats.get("total_reflections", 0)
                metrics.reflections_failed = (
                    stats.get("total_reflections", 0)
                    - stats.get("successes", 0)
                    - stats.get("partial", 0)
                    - stats.get("failures", 0)
                )
                metrics.evolution_triggers = stats.get("evolution_triggers", 0)
                logger.info(
                    "📊 Reflector final stats: reflections=%d successes=%d "
                    "failures=%d evolution_triggers=%d",
                    stats.get("total_reflections", 0),
                    stats.get("successes", 0),
                    stats.get("failures", 0),
                    stats.get("evolution_triggers", 0),
                )
        except Exception as exc:
            logger.debug("Reflector stats flush skipped (non-fatal): %s", exc)

        # Close LongTermMemory KV SQLite connection cleanly.
        # RISK: _kv_conn may be None if KV store was never initialised.
        try:
            kv_conn = getattr(long_term_memory, "_kv_conn", None)
            if kv_conn is not None:
                kv_conn.close()
                logger.debug("LongTermMemory KV store connection closed.")
        except Exception as exc:
            logger.debug("LongTermMemory KV close skipped (non-fatal): %s", exc)

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