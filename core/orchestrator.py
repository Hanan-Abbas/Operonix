"""
core/orchestrator.py — Operonix AI OS Agent

HYBRID EXECUTION CHANGE:
  profile_hint (from intent_parser) and cwd (from window_detector /
  pre_panel_context) are now carried through every event in the pipeline
  so the executor receives both without needing to re-query anything.

  New startup step: terminal_resolver.init() is awaited so the resolver
  can blacklist its own window before any command arrives.

  Flow additions:
    _handle_panel_input     → forwards profile_hint from panel payload
    handle_new_task         → stores profile_hint in active_tasks
    handle_context_snapshot → stores cwd in active_tasks context
    route_to_mapper         → stores profile_hint on task, passes it through
    inject_task_metadata    → merges profile_hint + cwd into task_dispatched
    handle_safety_cleared   → preserves profile_hint in task_dispatched_safe
    finalize_task / handle_failure → unchanged (profile_hint not needed there)
"""
from __future__ import annotations

import asyncio
import logging
import threading
import time
import uuid
from typing import Any, Optional

from core.config import settings
from core.event_bus import bus
from voice.audio_manager import AudioManager
from voice.wake_word import WakeWordDetector
from voice.pipeline import VoicePipeline

logger = logging.getLogger("Orchestrator")


def _panel_enabled() -> bool:
    return bool(getattr(settings, "PANEL_ENABLED", True))


def _build_panel_controller() -> Any:
    from panel.panel_controller import PanelController
    from brain.intent_parser import IntentParser
    from capabilities.registry import capability_registry
    from plugins.loader import plugin_loader
    from learning.retriever import Retriever

    _intent_parser = IntentParser()
    try:
        _retriever = Retriever()
        def learned_ranking(app: str, intent: str) -> list[str]:
            try:
                return _retriever.get_method_ranking(app=app, intent=intent)
            except Exception:
                return []
    except Exception:
        learned_ranking = None

    return PanelController(
        event_bus=bus,
        intent_parser=lambda text: _intent_parser.parse(text),
        plugin_registry=lambda app, intent: plugin_loader.find(app=app, intent=intent),
        capability_registry=lambda intent: capability_registry.find(intent=intent),
        learned_ranking=learned_ranking,
    )


class Orchestrator:
    def __init__(self) -> None:
        self.active_tasks: dict[str, dict[str, Any]] = {}
        self.is_running: bool = False
        self._voice_active = True

        self.audio_manager = AudioManager(
            rate=int(getattr(settings, "AUDIO_RATE", 16000)),
            chunk=int(getattr(settings, "AUDIO_CHUNK", 1280)),
            auto_start=False,
        )
        self.pipeline  = VoicePipeline(audio_manager=self.audio_manager)
        wake_phrase    = getattr(settings, "WAKE_WORD", "alexa")
        self.wake_detector = WakeWordDetector(
            wake_word=wake_phrase,
            audio_manager=self.audio_manager,
        )
        self._panel_controller: Optional[Any] = None
        self._panel_thread:     Optional[threading.Thread] = None
        self._panel_ready = threading.Event()

        # Dedup set: task_safety_cleared fires from multiple safety subscribers
        # (safety_validator, permission_guard, planner) for the same task_id.
        # Without this, the executor runs the plugin once per subscriber.
        self._dispatched_safe: set[str] = set()

        # Reflector instance — set during start() after the event loop is live.
        # Typed as Optional[Any] to avoid a hard import at module load time,
        # which would trigger LongTermMemory / EpisodicMemory DB connections
        # before the process is fully initialised.
        self._reflector: Optional[Any] = None

    async def start(self) -> None:
        self.is_running = True
        loop = asyncio.get_running_loop()
        self.wake_detector.loop = loop

        # ── Boot the Reflector ─────────────────────────────────────────────
        # Initialised HERE — before any bus.subscribe() calls — so it is
        # already subscribed to "execution_complete" when the first task runs.
        #
        # RISK: deferred imports guard against DB / model initialisation
        #   failures blocking the entire Orchestrator startup. If the
        #   Reflector fails to init, Operonix continues without reflection
        #   (degraded mode) and logs a warning.
        # RISK: LongTermMemory and EpisodicMemory constructors may open
        #   file handles / SQLite connections. We import lazily here (not at
        #   module top) so those I/O operations only happen after the event
        #   loop is running, avoiding "no current event loop" errors.
        try:
            from brain.reflector import Reflector
            from memory.episodic import EpisodicMemory
            from memory.long_term_memory import LongTermMemory
            from brain.llm_client import llm_client as _llm_client
            self._reflector = Reflector(
                event_bus  = bus,
                episodic   = EpisodicMemory(),
                long_term  = LongTermMemory(),
                llm_client = _llm_client,
                settings   = settings,
            )
            logger.info("Reflector initialised and subscribed to execution_complete.")
        except Exception as exc:
            logger.warning(
                "Reflector init failed — self-reflection disabled (degraded mode): %s",
                exc,
            )
            self._reflector = None

        bus.subscribe("wake_word_detected",    self.handle_wake_word)
        bus.subscribe("text_query_received",   self._handle_panel_input)
        bus.subscribe("user_input_received",   self.handle_new_task)
        bus.subscribe("context_snapshot_ready", self.handle_context_snapshot)
        bus.subscribe("intent_parsed",         self.route_to_mapper)
        bus.subscribe("capability_mapped",     self.inject_task_metadata)
        bus.subscribe("task_failed",            self.handle_failure)
        bus.subscribe("task_completed",         self.finalize_task)
        # Resume execution after user approves a high-risk confirmation.
        bus.subscribe("task_safety_cleared",    self.handle_safety_cleared)
        # Mark tasks as high-risk so _check_low_risk_dispatch doesn't run them
        # before the user approves them.
        bus.subscribe("confirmation_required",  self._mark_high_risk)

        # Start the three-layer safety stack so each subscribes to its events.
        # Order matters: permission_guard must be first (fastest gate).
        from safety.permission_guard import permission_guard
        from safety.validator import safety_validator
        from safety.confirmation import confirmation_manager
        await permission_guard.start()      # gate 1 — fast intent-level check
        await safety_validator.start()      # gate 2 — deep per-step analysis
        await confirmation_manager.start()  # human-in-the-loop bridge

        # ── Hybrid execution: initialise terminal resolver ─────────────────
        # This must happen before any command arrives so the resolver can
        # blacklist its own window and start the focus-stack polling loop.
        try:
            from core.terminal_resolver import terminal_resolver
            await terminal_resolver.init()
            logger.info("TerminalResolver initialised.")
        except Exception as exc:
            logger.warning("TerminalResolver init failed — commands will use Ghost profile: %s", exc)

        asyncio.create_task(self._background_wake_loop())
        asyncio.create_task(self._emit_app_context_loop())

        logger.info(
            "Orchestrator started (wake-word=%r, panel=%s).",
            self.wake_detector.wake_word,
            "enabled" if _panel_enabled() else "disabled",
        )

    async def stop(self) -> None:
        self.is_running = False
        self.audio_manager.stop()
        if self._panel_controller is not None:
            try:
                loop = asyncio.get_running_loop()
                await loop.run_in_executor(None, self._stop_panel)
            except Exception as exc:
                logger.warning("panel stop error: %s", exc)
        logger.info("Orchestrator stopped.")

    def _start_panel(self) -> None:
        if self._panel_thread is not None and self._panel_thread.is_alive():
            return
        self._panel_ready.clear()
        self._panel_thread = threading.Thread(
            target=self._panel_thread_target,
            name="operonix-panel",
            daemon=True,
        )
        self._panel_thread.start()
        ready = self._panel_ready.wait(
            timeout=float(getattr(settings, "PANEL_START_TIMEOUT", 10.0))
        )
        if not ready:
            logger.warning("panel did not signal ready within timeout.")

    def _stop_panel(self) -> None:
        if self._panel_controller is not None:
            try:
                self._panel_controller.stop()
            except Exception as exc:
                logger.warning("panel_controller.stop() error: %s", exc)
            self._panel_controller = None
        if self._panel_thread is not None and self._panel_thread.is_alive():
            self._panel_thread.join(timeout=5.0)
            self._panel_thread = None
        logger.info("panel stopped.")

    def _panel_thread_target(self) -> None:
        try:
            from PyQt6.QtWidgets import QApplication
            import sys
            from pathlib import Path
            state_dir = Path.home() / ".operonix"
            state_dir.mkdir(parents=True, exist_ok=True)
            qt_app = QApplication.instance() or QApplication(sys.argv)
            self._panel_controller = _build_panel_controller()
            self._panel_controller.start(loop=None)
            self._panel_ready.set()
            logger.info("panel thread ready.")
            qt_app.exec()
        except ImportError as exc:
            logger.warning("PyQt6 not available — panel disabled (%s).", exc)
            self._panel_ready.set()
        except Exception as exc:
            logger.error("panel thread crashed: %s", exc, exc_info=True)
            self._panel_ready.set()

    async def _background_wake_loop(self) -> None:
        loop = asyncio.get_running_loop()
        while self.is_running:
            if not getattr(self, "_voice_active", True):
                await asyncio.sleep(0.05)
                continue
            future = loop.run_in_executor(None, self.wake_detector.detect)
            try:
                await asyncio.wait_for(asyncio.shield(future), timeout=1.0)
            except asyncio.TimeoutError:
                pass
            await asyncio.sleep(0.005)

    async def _emit_app_context_loop(self) -> None:
        interval = float(getattr(settings, "APP_CONTEXT_POLL_INTERVAL", 2.0))
        last_app: str = ""
        while self.is_running:
            await asyncio.sleep(interval)
            try:
                from context.app_profiler import AppProfiler
                current_app = AppProfiler.get_active_app_name()
                if current_app and current_app != last_app:
                    last_app = current_app
                    await bus.emit(
                        "app_context_changed",
                        {"app_name": current_app},
                        source="orchestrator",
                    )
            except Exception as exc:
                logger.debug("app_profiler error: %s", exc)

    async def handle_wake_word(self, event: Any) -> None:
        score = event.data.get("score", 0.0)
        logger.info("Wake word detected (score=%.2f)", score)
        loop = asyncio.get_running_loop()
        try:
            command = await loop.run_in_executor(None, self.pipeline.capture_command)
            if command and command.get("text"):
                await bus.emit(
                    "user_input_received",
                    {
                        "text":             command["text"],
                        "source":           "voice",
                        "stt":              command.get("stt", {}),
                        "stt_provider":     command.get("provider"),
                        "confidence":       command.get("confidence", 0.0),
                        "duration":         command.get("duration_seconds", 0),
                        "preferred_method": None,
                        "profile_hint":     None,   # voice has no hint; resolver decides
                    },
                    source="orchestrator",
                )
        except Exception as exc:
            logger.error("Voice capture failed: %s", exc)
            await bus.emit("voice_capture_error", {"error": str(exc)}, source="orchestrator")

    async def _handle_panel_input(self, event: Any) -> None:
        """
        Translate text_query_received (from panel) → user_input_received.

        Now also forwards:
          profile_hint     — set by intent_parser.parse() in the panel's
                             suggestion engine; carries "ghost"/"bridge"/"lab"
                             so the executor does not need a second LLM call.
          cwd              — injected by panel_controller from HotkeyListener's
                             pre_panel_context so we get the user's real cwd
                             even after the panel window has taken focus.
          pre_panel_context — full context snapshot taken at hotkey press time.
        """
        query = (event.data.get("query") or "").strip()
        if not query:
            return

        await bus.emit(
            "user_input_received",
            {
                "text":               query,
                "source":             "panel",
                "stt":                {},
                "stt_provider":       None,
                "confidence":         1.0,
                "duration":           0,
                "preferred_method":   event.data.get("preferred_method"),
                # ── HYBRID EXECUTION ───────────────────────────────────────
                # profile_hint was computed by intent_parser.parse() during
                # live suggestion — carry it so validate_and_route doesn't
                # re-parse and the executor gets it immediately.
                "profile_hint":       event.data.get("profile_hint"),
                # cwd and pre_panel_context from HotkeyListener snapshot.
                "cwd":                event.data.get("cwd"),
                "pre_panel_context":  event.data.get("pre_panel_context"),
            },
            source="orchestrator",
        )

    async def handle_new_task(self, event: Any) -> None:
        task_id   = str(uuid.uuid4())[:8]
        user_text = (event.data.get("text") or "").strip()
        if not user_text:
            return

        self.active_tasks[task_id] = {
            "status":           "gathering_context",
            "input":            user_text,
            "source":           event.data.get("source", "unknown"),
            "preferred_method": event.data.get("preferred_method"),
            # ── HYBRID EXECUTION: store profile_hint and cwd on the task ──
            "profile_hint":     event.data.get("profile_hint"),
            "cwd":              event.data.get("cwd"),
            "context":          {},
            "started_at":       time.monotonic(),
        }
        logger.info(
            "Task [%s] initialised (source=%s, profile_hint=%s): %r",
            task_id,
            self.active_tasks[task_id]["source"],
            self.active_tasks[task_id]["profile_hint"],
            user_text,
        )

        pre_panel_context = event.data.get("pre_panel_context")
        snapshot_payload  = {"task_id": task_id}
        if pre_panel_context:
            snapshot_payload["pre_panel_context"] = pre_panel_context
            logger.debug(
                "Task [%s] forwarding pre_panel_context cwd=%s",
                task_id, pre_panel_context.get("cwd"),
            )

        await bus.emit("request_context_snapshot", snapshot_payload, source="orchestrator")
        await bus.emit(
            "request_intent_parsing",
            {
                "task_id":          task_id,
                "text":             user_text,
                "stt":              event.data.get("stt") or {},
                "stt_provider":     event.data.get("stt_provider"),
                "preferred_method": event.data.get("preferred_method"),
                # ── HYBRID EXECUTION: carry profile_hint into LLM pipeline ─
                # intent_parser.validate_and_route reads this so it can skip
                # re-computing the keyword hint when one is already known.
                "profile_hint":     event.data.get("profile_hint"),
            },
            source="orchestrator",
        )

    async def handle_context_snapshot(self, event: Any) -> None:
        task_id = event.data.get("task_id")
        if not task_id or task_id not in self.active_tasks:
            return

        snapshot = {k: v for k, v in event.data.items() if k != "task_id"}
        current_ctx = self.active_tasks[task_id]["context"]
        for key, value in snapshot.items():
            if not current_ctx.get(key):
                current_ctx[key] = value

        # ── HYBRID EXECUTION: promote cwd from task to context if snapshot
        # did not provide one (e.g. window_detector had stale data).
        if not current_ctx.get("cwd") and self.active_tasks[task_id].get("cwd"):
            current_ctx["cwd"] = self.active_tasks[task_id]["cwd"]
            logger.debug(
                "Task [%s] promoted cwd from task store to context: %s",
                task_id, current_ctx["cwd"],
            )

        logger.debug(
            "Task [%s] context: window='%s' cwd='%s'",
            task_id,
            current_ctx.get("window_title", ""),
            current_ctx.get("cwd", "<not set>"),
        )

    async def route_to_mapper(self, event: Any) -> None:
        task_id = event.data.get("task_id")
        task    = self.active_tasks.get(task_id, {})
        if event.data.get("intent"):
            task["intent"] = event.data["intent"]

        # ── HYBRID EXECUTION: store profile_hint on task if the LLM returned
        # one (intent_parser.validate_and_route injects it into intent_parsed).
        incoming_hint = event.data.get("profile_hint")
        if incoming_hint and not task.get("profile_hint"):
            task["profile_hint"] = incoming_hint
            logger.debug(
                "Task [%s] profile_hint set from intent_parsed: %s",
                task_id, incoming_hint,
            )

        await bus.emit("request_capability_mapping", event.data, source="orchestrator")

    async def _mark_high_risk(self, event: Any) -> None:
        """
        Subscribes to confirmation_required.
        Sets _high_risk_pending=True on the task so _check_low_risk_dispatch
        knows NOT to auto-dispatch — the user must approve first.
        """
        task_id = event.data.get("task_id")
        task    = self.active_tasks.get(task_id)
        if task is not None:
            task["_high_risk_pending"] = True
            logger.debug("Task [%s] marked as high-risk — awaiting user confirmation.", task_id)

    async def handle_safety_cleared(self, event: Any) -> None:
        """
        Called when a task_safety_cleared event arrives.

        ROOT CAUSE FIX — pre-approval execution:
        ─────────────────────────────────────────
        task_safety_cleared is published by TWO different components:

          1. safety_validator  — fires immediately when a task passes static
                                 risk rules. For HIGH-RISK tasks it publishes
                                 BOTH confirmation_required AND task_safety_cleared
                                 in the same handler. This is premature — the
                                 user has not approved anything yet.

          2. confirmation_manager — fires AFTER the user clicks Allow in the
                                    panel or dashboard. This is the legitimate
                                    trigger for execution.

        The previous dedup guard (_dispatched_safe) blocked the second arrival
        but let the first through — the wrong one. The fix is to check the
        event source and only route to the executor when it comes from
        confirmation_manager. When it comes from safety_validator we store it
        as "pending" and wait for confirmation_manager to confirm it.

        For LOW-RISK tasks that skip confirmation entirely, safety_validator
        publishes task_safety_cleared without a matching confirmation_required.
        In that case confirmation_manager never fires, so we use a short timeout
        to detect "no confirmation came" and route the pending task directly.
        """
        task_id = event.data.get("task_id")
        task    = self.active_tasks.get(task_id, {})
        source  = getattr(event, "source", None) or event.data.get("_source", "")

        # ── CASE 1: Source is confirmation_manager → user just approved ───────
        # This is the ONLY legitimate trigger for high-risk task execution.
        if source == "confirmation_manager":
            if task_id in self._dispatched_safe:
                logger.debug(
                    "Task [%s] already dispatched — ignoring late confirmation_manager event.",
                    task_id,
                )
                return
            self._dispatched_safe.add(task_id)
            logger.info("Task [%s] approved by user — dispatching to executor.", task_id)
            await self._dispatch_to_executor(event, task)
            return

        # ── CASE 2: Source is safety_validator / permission_guard / planner ───
        # This fires BEFORE the user sees any confirmation dialog for high-risk
        # tasks. We must NOT dispatch immediately.
        #
        # Strategy: store the event payload as "pending_safe" on the task and
        # schedule a short-window check. If confirmation_manager fires within
        # 10 s, it will dispatch (Case 1). If no confirmation_required was
        # published (low-risk task), we dispatch after a brief yield.
        if task_id in self._dispatched_safe:
            logger.debug(
                "Task [%s] already dispatched — ignoring duplicate from '%s'.",
                task_id, source,
            )
            return

        # Store the event for potential low-risk passthrough
        if task:
            task["_pending_safe_event"] = event
            task["_pending_safe_source"] = source

        # Check whether confirmation_required was already published for this task.
        # If it was, this task is high-risk — do NOT dispatch; wait for user.
        # If it was NOT published within a brief window, it's low-risk — dispatch.
        asyncio.get_event_loop().call_later(
            0.1,  # 100 ms window — confirmation_required fires in the same batch
            lambda: asyncio.ensure_future(
                self._check_low_risk_dispatch(task_id)
            ),
        )

    async def _check_low_risk_dispatch(self, task_id: str) -> None:
        """
        Called 100 ms after safety_validator publishes task_safety_cleared.

        By this point, if the task is high-risk, confirmation_required will
        have already been published and confirmation_manager will be waiting
        for user input. In that case _dispatched_safe is still empty for this
        task_id and task["_high_risk_pending"] will be True.

        If no confirmation was needed (low-risk), we dispatch now.
        """
        if task_id in self._dispatched_safe:
            # Already dispatched by confirmation_manager path — nothing to do.
            return

        task = self.active_tasks.get(task_id, {})

        # If confirmation_manager is holding this task, do NOT dispatch.
        # The flag is set by _mark_high_risk below.
        if task.get("_high_risk_pending"):
            logger.debug(
                "Task [%s] is high-risk and awaiting user confirmation — not dispatching.",
                task_id,
            )
            return

        # Low-risk task — safe to dispatch directly.
        pending_event = task.get("_pending_safe_event")
        if pending_event is None:
            return

        self._dispatched_safe.add(task_id)
        logger.info(
            "Task [%s] is low-risk — dispatching directly (no confirmation needed).",
            task_id,
        )
        await self._dispatch_to_executor(pending_event, task)

    async def _dispatch_to_executor(self, event: Any, task: dict) -> None:
        """
        Shared dispatch logic: enrich payload and emit task_dispatched_safe.
        Previously inlined in handle_safety_cleared.
        """
        task_id = event.data.get("task_id")
        payload = dict(event.data)

        # Promote full_task.steps to top-level so executor finds them
        if not payload.get("steps"):
            full_task_steps = (payload.get("full_task") or {}).get("steps") or []
            if full_task_steps:
                payload["steps"] = full_task_steps
                logger.debug(
                    "Task [%s] promoted %d steps from full_task to top level",
                    task_id, len(full_task_steps),
                )

        # Merge accumulated context (real terminal cwd, window_title, etc.)
        if task:
            accumulated_ctx = task.get("context") or {}
            existing_ctx    = payload.get("context") or {}
            payload["context"] = {**accumulated_ctx, **existing_ctx}

        # Restore profile_hint if it survived the safety pause
        if not payload.get("profile_hint") and task.get("profile_hint"):
            payload["profile_hint"] = task["profile_hint"]

        # Inject preferred_method — confirmation_manager re-publishes the
        # confirmation_required payload which was built by intent_parser and
        # never includes preferred_method. Without it executor sees None and
        # runs focus_manager, causing "Failed to focus target window" failures.
        if not payload.get("preferred_method") and task.get("preferred_method"):
            payload["preferred_method"] = task["preferred_method"]
        # Also check full_task for preferred_method
        if not payload.get("preferred_method"):
            full_task_pm = (payload.get("full_task") or {}).get("preferred_method")
            if full_task_pm:
                payload["preferred_method"] = full_task_pm

        # Push profile_hint into each step's args
        steps = payload.get("steps") or []
        hint  = payload.get("profile_hint")
        if hint and steps:
            for step in steps:
                step.setdefault("args", {})
                if "profile_hint" not in step["args"]:
                    step["args"]["profile_hint"] = hint

        await bus.emit("task_dispatched_safe", payload, source="orchestrator")

    async def inject_task_metadata(self, event: Any) -> None:
        task_id = event.data.get("task_id")
        if not task_id or task_id not in self.active_tasks:
            return

        task    = self.active_tasks[task_id]
        ctx     = task.get("context") or {}
        pmethod = task.get("preferred_method")

        if pmethod and not event.data.get("preferred_method"):
            event.data["preferred_method"] = pmethod

        existing = event.data.get("context") or {}
        event.data["context"] = {**ctx, **existing}

        await asyncio.sleep(0)

        # ── HYBRID EXECUTION: inject profile_hint into task_dispatched ─────
        # The executor reads profile_hint from the task's steps[].args so it
        # can pass it straight to terminal_resolver.resolve() without a second
        # LLM round-trip.
        profile_hint = (
            event.data.get("profile_hint")      # already on event (from intent_parsed)
            or task.get("profile_hint")          # stored during handle_new_task
        )

        logger.debug(
            "Task [%s] metadata injected (preferred_method=%s, cwd=%s, profile_hint=%s)",
            task_id,
            event.data.get("preferred_method"),
            event.data["context"].get("cwd", "<not set>"),
            profile_hint,
        )

        # Build steps — inject profile_hint into args so executor/shell_tool
        # can read it via args["profile_hint"].
        base_args = {**event.data.get("parameters", {})}
        if profile_hint:
            base_args["profile_hint"] = profile_hint

        await bus.emit(
            "task_dispatched",
            {
                "task_id":          task_id,
                "intent":           event.data.get("intent"),
                "capability":       event.data.get("capability"),
                "parameters":       event.data.get("parameters", {}),
                "suggested_tool":   event.data.get("suggested_tool"),
                "preferred_method": event.data.get("preferred_method"),
                "profile_hint":     profile_hint,
                "context":          event.data["context"],
                "steps": event.data.get("steps") or [
                    {
                        "action": event.data.get("intent"),
                        "args":   base_args,
                    }
                ],
            },
            source="orchestrator",
        )

    async def handle_failure(self, event: Any) -> None:
        task_id    = event.data.get("task_id")
        error      = event.data.get("error")
        task       = self.active_tasks.get(task_id, {})
        # Delay clearing so late task_safety_cleared duplicates are still blocked
        asyncio.get_event_loop().call_later(5.0, self._dispatched_safe.discard, task_id)
        logger.error("Task [%s] failed: %s", task_id, error)
        elapsed_ms = int(
            (time.monotonic() - task.get("started_at", time.monotonic())) * 1000
        )
        await bus.emit(
            "action_completed",
            {
                "task_id":     task_id,
                "query":       task.get("input", ""),
                "intent":      task.get("intent"),
                "method":      task.get("method_used", "unknown"),
                "success":     False,
                "duration_ms": elapsed_ms,
                "error":       error,
            },
            source="orchestrator",
        )
        await bus.emit(
            "error_detected",
            {"task_id": task_id, "error": error, "context": task.get("context")},
            source="orchestrator",
        )

    async def finalize_task(self, event: Any) -> None:
        task_id    = event.data.get("task_id")
        task       = self.active_tasks.pop(task_id, {})
        # Delay clearing so late task_safety_cleared duplicates are still blocked
        asyncio.get_event_loop().call_later(5.0, self._dispatched_safe.discard, task_id)

        # ── Store method_used so metrics, learning, and Reflector stats ──────
        # have the resolved tier ("plugin"/"api"/"command"/"ui") on the task
        # record even after active_tasks is popped above.
        # RISK: event.data.get() is safe — event.data is always a dict here
        #   (enforced by the EventBus shims).
        if event.data.get("method_used"):
            task["method_used"] = event.data["method_used"]

        elapsed_ms = int(
            (time.monotonic() - task.get("started_at", time.monotonic())) * 1000
        )
        logger.info("Task [%s] completed in %d ms.", task_id, elapsed_ms)
        await bus.emit(
            "action_completed",
            {
                "task_id":     task_id,
                "query":       task.get("input", ""),
                "intent":      event.data.get("intent") or task.get("intent"),
                "method":      event.data.get("method_used") or task.get("method_used", "unknown"),
                "success":     True,
                "duration_ms": elapsed_ms,
            },
            source="orchestrator",
        )


orchestrator = Orchestrator()

if __name__ == "__main__":
    logging.basicConfig(level=logging.INFO)

    async def _main() -> None:
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
        print("\nOrchestrator stopped.")