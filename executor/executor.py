"""
executor/executor.py
─────────────────────
Central task executor — updated to consume MethodDecision from MethodRouter.

Changes from original (plan sections referenced inline)
─────────────────────────────────────────────────────────
PLAN §3.1 — Routing removed from executor
    The executor no longer makes routing decisions.  It consumes the
    MethodDecision produced by MethodRouter.select() and dispatched by
    core/orchestrator.py.  The old _build_waterfall() / _WATERFALL_ORDER
    ad-hoc order is replaced by MethodDecision.fallback_chain (a frozen
    tuple, never mutated — Optimization A).

PLAN §4.1 / Gap 1 — FailureClass tagging at the executor boundary
    Every exception caught inside _execute_with_decision() is passed to
    error_classifier.from_exception() which returns a FailureClass.
    The FailureClass is stamped into the MethodDecision via
    decision.with_failure() before the event is published so the learner
    and event bus receive a typed, structured failure — never a raw
    exception string.

    Decision table enforced here:
      ENV_TRANSIENT    → retry same method (backoff ×MAX_RETRY_ATTEMPTS),
                         never descend fallback, never feed learner
      ROUTING_MISMATCH → no retry, descend fallback_chain, feed learner
      ENV_PERMANENT    → no retry, mark method unavailable, descend fallback
      EXECUTION_LOGIC  → no retry, no fallback, surface to debugger

PLAN §4.2 / Gap 2 — LayeredPayload slot consumption
    On each fallback step the executor calls
    decision.payload.for_method(next_method) to read the pre-serialized
    slot.  No translation logic exists in this file.

PLAN §4.3 / Gap 3 — UIReadinessGuard (JIT focus validation)
    UIReadinessGuard.validate() is called immediately before any UI tool
    invocation — not at routing time.  FocusDriftError and
    UIStateMismatchError are tagged ENV_TRANSIENT so the learner never
    penalizes the UI method for a user's cursor movement.

All existing fixes are preserved unchanged:
    CAVEAT 1 — dual subscription dedup
    CAVEAT 3 — Bridge pre-flight permission check
    CAVEAT 4 — output truncation
    BUG 1, 2, 3 — steps unwrapping, task_id stamping, context enrichment
    REFLECTOR integration (execution_complete on success + failure)
    CONCURRENCY — background asyncio.Task per plan
    RISK R1-R5 — Reflector payload safety mitigations
"""
from __future__ import annotations

import asyncio
import logging
import os
import platform
import re
import time
from typing import Any

from capabilities.registry import capability_registry
from core.config import settings
from core.error_handler import ErrorHandler
from core.event_bus import bus
from core.terminal_resolver import (
    AmbiguousTarget,
    BridgeTarget,
    GhostTarget,
    LabTarget,
    terminal_resolver,
)
from executor.error_classifier import error_classifier
from executor.fallback_manager import FallbackManager
from executor.focus_manager import FocusManager
from executor.retry_manager import RetryManager
from core.metrics import metrics
from brain.llm_client import llm_client
from tools.tool_registry import tool_registry
from tools.tool_selector import tool_selector
from safety.risk_rules import truncate_output
from tools.routing_decision import FailureClass, MethodDecision, MethodType

logger = logging.getLogger("Executor")

error_handler    = ErrorHandler(event_bus=bus, logger=logger)
retry_manager    = RetryManager()
fallback_manager = FallbackManager()
focus_manager    = FocusManager()

# ── Legacy waterfall constants (kept for backward compat with capability_registry path) ──
_WATERFALL_ORDER: list[str] = ["plugin", "api", "command", "ui"]

_TIER_TO_TOOL_TYPE: dict[str, str] = {
    "plugin":  "plugin",
    "api":     "api_tool",
    "command": "shell_tool",
    "ui":      "ui_tool",
}

_SHELL_ACTIONS: frozenset[str] = frozenset({
    "run_command", "execute", "git_op", "check_status",
    "execute_script", "navigate",
})


# ─────────────────────────────────────────────────────────────────────────────
# JIT UI readiness guard (Gap 3 + Optimization B)
# ─────────────────────────────────────────────────────────────────────────────

class FocusDriftError(RuntimeError):
    """Raised when the focused app has changed since routing time."""
    def __init__(self, expected: str, actual: str):
        super().__init__(
            f"Focus drift: expected app '{expected}', actual '{actual}'"
        )
        self.expected = expected
        self.actual   = actual


class UIStateMismatchError(RuntimeError):
    """Raised when the live AX tree does not match the routing-time snapshot."""
    def __init__(self, detail: str = ""):
        super().__init__(f"UI state mismatch: {detail}")


class UIReadinessGuard:
    """
    Two-checkpoint JIT validator for the UI execution layer (Gap 3).

    Called by the executor immediately before any UI tool invocation —
    never at routing time.

    Checkpoint 1 (static, in MethodRouter._evaluate_ui) — already done:
        Is this intent class ever compatible with UI?

    Checkpoint 2 (JIT, here) — done at execution time T+N:
        Right now, is the correct app focused and is the expected UI
        state present in the live accessibility tree?

    On failure:
        FocusDriftError       → tagged ENV_TRANSIENT; one re-focus attempt
                                 via focus_manager before aborting.
        UIStateMismatchError  → tagged ENV_TRANSIENT; abort without retry.

    Optimization B: context_validator.snapshot() is called with
        force_refresh=True and invalidate_handles=True so no cached
        accessibility tree can defeat the live check.
    """

    # AX query hard timeout (Optimization B requirement 4)
    _AX_TIMEOUT_S: float = float(
        getattr(settings, "UI_AX_TIMEOUT_SECONDS", 0.15)
    )

    def validate(
        self,
        decision: MethodDecision,
    ) -> None:
        """
        Synchronous validate — called from the executor's async context via
        asyncio.get_running_loop().run_in_executor() so the AX syscall
        does not block the event loop.

        Raises FocusDriftError or UIStateMismatchError on failure.
        Does nothing (returns None) on success.
        """
        expected_app = decision.expected_app
        if not expected_app:
            # No app constraint recorded at routing time — skip focus check.
            # This happens for intent-only UI actions where no window was open.
            return

        # ── 1. Live focus check (no cache) ────────────────────────────────
        try:
            from context.focus_tracker import focus_tracker
            # focus_tracker.last_known_title is updated by the async monitor loop.
            # For the JIT check we trigger a fresh OS query via the internal
            # method rather than reading the cached attribute.
            current_title: str = ""
            if hasattr(focus_tracker, "_get_current_foreground_title"):
                # Run the coroutine synchronously inside this thread executor
                import asyncio as _asyncio
                try:
                    loop = _asyncio.get_event_loop()
                    current_title = loop.run_until_complete(
                        asyncio.wait_for(
                            focus_tracker._get_current_foreground_title(),
                            timeout=self._AX_TIMEOUT_S,
                        )
                    )
                except Exception:
                    current_title = getattr(focus_tracker, "last_known_title", "") or ""
            else:
                current_title = getattr(focus_tracker, "last_known_title", "") or ""

            # Fuzzy match (same logic as focus_tracker.check_focus_alignment)
            expected_lower = expected_app.lower()
            actual_lower   = current_title.lower()
            focused = (
                expected_lower in actual_lower
                or actual_lower in expected_lower
            )
            if not focused:
                raise FocusDriftError(
                    expected=expected_app,
                    actual=current_title or "unknown",
                )
        except FocusDriftError:
            raise
        except Exception as exc:
            logger.warning(
                "UIReadinessGuard: focus check failed with unexpected error: %s "
                "— treating as drift.", exc,
            )
            raise FocusDriftError(expected=expected_app, actual="error")

        # ── 2. Live accessibility tree check (Optimization B) ────────────
        expected_ui_state = decision.expected_ui_state
        if expected_ui_state is None:
            return   # no snapshot was taken at routing time — skip AX check

        try:
            from context.context_validator import context_validator
            if hasattr(context_validator, "snapshot"):
                try:
                    tree = asyncio.get_event_loop().run_until_complete(
                        asyncio.wait_for(
                            context_validator.snapshot(
                                force_refresh=True,
                                invalidate_handles=True,
                            ),
                            timeout=self._AX_TIMEOUT_S,
                        )
                    )
                except asyncio.TimeoutError:
                    # AX query timed out — treat as ENV_TRANSIENT (Opt B req 4)
                    raise UIStateMismatchError(
                        f"AX tree query timed out after {self._AX_TIMEOUT_S:.0f}s "
                        f"— system under load"
                    )
                if hasattr(tree, "matches") and not tree.matches(expected_ui_state):
                    raise UIStateMismatchError(
                        "live AX tree does not match routing-time snapshot"
                    )
        except (FocusDriftError, UIStateMismatchError):
            raise
        except Exception as exc:
            logger.warning(
                "UIReadinessGuard: AX snapshot failed: %s — skipping state check.",
                exc,
            )
            # AX query unavailable — do not abort; focus check already passed.


_ui_readiness_guard = UIReadinessGuard()


# ─────────────────────────────────────────────────────────────────────────────
# Executor
# ─────────────────────────────────────────────────────────────────────────────

class Executor:

    def __init__(self) -> None:
        self.os_name = platform.system()
        self.is_running = False
        self.restricted_actions: set[str] = set()
        self._pending_target_selections: dict[str, asyncio.Future] = {}

        # CAVEAT 1 — dedup guard
        self._seen_task_ids: set[str] = set()

        # Concurrent execution registry: task_id → asyncio.Task
        self._running_tasks: dict[str, asyncio.Task] = {}

    async def start(self) -> None:
        bus.subscribe("task_dispatched_safe", self.execute_plan)
        bus.subscribe("target_selected",      self._on_target_selected)
        self.is_running = True
        logger.info("Executor Online | OS: %s", self.os_name)
        logger.info("Tools Loaded: %d", len(tool_registry.list_tools()))

    # ── Main entry point ───────────────────────────────────────────────────

    async def execute_plan(self, event: Any) -> None:
        """
        Receive a task_dispatched_safe event and dispatch it as a background
        asyncio.Task so the event loop stays free (CONCURRENCY fix).
        """
        task_data = event.data
        task_id   = task_data.get("task_id")

        if task_id in self._seen_task_ids:
            logger.debug(
                "execute_plan: task [%s] already running — ignoring duplicate.",
                task_id,
            )
            return
        self._seen_task_ids.add(task_id)

        task = asyncio.create_task(
            self._run_plan(event),
            name=f"operonix-task-{task_id}",
        )
        self._running_tasks[task_id] = task

        def _on_done(t: asyncio.Task) -> None:
            self._running_tasks.pop(task_id, None)
            asyncio.get_event_loop().call_later(
                5.0, self._seen_task_ids.discard, task_id
            )
            if t.cancelled():
                logger.info("Task [%s] was cancelled.", task_id)
            elif t.exception():
                logger.error("Task [%s] raised: %s", task_id, t.exception())

        task.add_done_callback(_on_done)
        bus.publish(
            "task_queued",
            {"task_id": task_id, "concurrent_count": len(self._running_tasks)},
            source="executor",
        )
        logger.info(
            "Task [%s] dispatched (%d running).", task_id, len(self._running_tasks)
        )

    # ── Plan runner ────────────────────────────────────────────────────────

    async def _run_plan(self, event: Any) -> None:
        """
        Execute the plan steps.  Each step is dispatched via
        _execute_step_safe() which uses the MethodDecision from the
        orchestrator when present, or falls back to the legacy capability
        waterfall for backward compatibility.
        """
        metrics.total_tasks += 1
        start     = time.time()
        task_data = event.data
        task_id   = task_data.get("task_id")

        # BUG 1 FIX — unwrap steps from full_task when top-level is empty
        steps = task_data.get("steps") or []
        if not steps:
            full_task = task_data.get("full_task") or {}
            steps = full_task.get("steps") or []
            if steps:
                logger.debug(
                    "Task [%s] steps unwrapped from full_task (%d steps)",
                    task_id, len(steps),
                )

        context          = task_data.get("context") or {}
        intent           = task_data.get("intent")
        preferred_method = task_data.get("preferred_method")

        # ── MethodDecision from orchestrator (Plan §3.1) ──────────────────
        # When present, the executor uses this as the sole routing authority.
        # When absent (legacy tasks), it falls back to the capability waterfall.
        method_decision: MethodDecision | None = task_data.get("method_decision")

        self._enrich_context_with_cwd(context)

        # BUG 3 FIX — enrich context from full_task when top-level is bare
        if not context.get("cwd"):
            full_task_ctx = (task_data.get("full_task") or {}).get("context") or {}
            if full_task_ctx:
                context.update(
                    {k: v for k, v in full_task_ctx.items() if not context.get(k)}
                )
            if not context.get("cwd"):
                params = (task_data.get("full_task") or {}).get("parameters") or {}
                if params.get("cwd"):
                    context["cwd"] = params["cwd"]
            self._enrich_context_with_cwd(context)

        # CAVEAT 1 — normalise profile_hint into every step's args
        profile_hint_top = task_data.get("profile_hint")
        if not profile_hint_top and steps:
            profile_hint_top = steps[0].get("args", {}).get("profile_hint")
        if profile_hint_top:
            for step in steps:
                step.setdefault("args", {})
                if "profile_hint" not in step["args"]:
                    step["args"]["profile_hint"] = profile_hint_top

        logger.info(
            "Starting Task [%s] — %d steps | preferred=%s | profile=%s | "
            "decision=%s",
            task_id, len(steps),
            preferred_method or "auto",
            profile_hint_top or "auto",
            method_decision.method.value if method_decision else "legacy",
        )

        method_used: str = "unknown"

        for step_index, step in enumerate(steps):
            action = step.get("action")
            bus.publish(
                "execution_step_started",
                {"task_id": task_id, "step_index": step_index, "action": action},
                source="executor",
            )

            if method_decision is not None:
                # ── New path: MethodDecision-driven execution ─────────────
                success, result, step_method = await self._execute_with_decision(
                    task_id, step_index, step, context, method_decision
                )
            else:
                # ── Legacy path: capability waterfall (backward compat) ───
                success, result, step_method = await self._execute_step_safe(
                    task_id, step_index, step, context,
                    preferred_method=preferred_method,
                )

            if step_method:
                method_used = step_method

            if not success:
                bus.publish(
                    "task_failed",
                    {
                        "task_id":     task_id,
                        "failed_step": step,
                        "error":       result,
                        "intent":      intent,
                        "method_used": method_used,
                    },
                    source="executor",
                )
                # REFLECTOR FEED — failure (RISK R1-R5)
                try:
                    _err_str  = None
                    _err_type = None
                    if isinstance(result, str):
                        _err_str = result
                    elif isinstance(result, dict):
                        _err_str  = result.get("message") or str(result)
                        _err_type = result.get("type")
                    else:
                        _err_str = str(result) if result is not None else None

                    bus.publish(
                        "execution_complete",
                        {
                            "task_id":     task_id,
                            "intent":      intent,
                            "capability":  method_used,
                            "app_context": (
                                context.get("app_name")
                                or context.get("window_title")
                                or "unknown"
                            ),
                            "success":     False,
                            "partial":     False,
                            "error":       _err_str,
                            "error_type":  _err_type,
                            "steps":       steps[: step_index + 1],
                            "duration_ms": round(
                                (time.time() - start) * 1000, 1
                            ),
                        },
                        source="executor",
                    )
                except Exception as ref_exc:
                    logger.debug(
                        "execution_complete (failure) publish error (non-fatal): %s",
                        ref_exc,
                    )

                retry_manager.clear_task(task_id)
                logger.error(
                    "Task [%s] failed at step %d: %s", task_id, step_index, result
                )
                return

            display_result = (
                truncate_output(result) if isinstance(result, str) else result
            )
            context["last_result"] = display_result
            context["last_action"] = action
            bus.publish(
                "execution_step_success",
                {
                    "task_id":    task_id,
                    "step_index": step_index,
                    "result":     display_result,
                },
                source="executor",
            )
            logger.info("Step %d completed: %s", step_index, action)

        elapsed = time.time() - start
        metrics.total_duration_seconds += elapsed
        metrics.successful_tasks += 1

        logger.info(
            "Success rate: %.1f%% | Avg duration: %.2fs",
            metrics.success_rate(), metrics.avg_task_duration(),
        )

        bus.publish(
            "task_completed",
            {
                "task_id":     task_id,
                "intent":      intent,
                "steps":       steps,
                "method_used": method_used,
            },
            source="executor",
        )

        # REFLECTOR FEED — success (RISK R1-R2-R4)
        try:
            bus.publish(
                "execution_complete",
                {
                    "task_id":     task_id,
                    "intent":      intent,
                    "capability":  method_used,
                    "app_context": (
                        context.get("app_name")
                        or context.get("window_title")
                        or "unknown"
                    ),
                    "success":     True,
                    "partial":     False,
                    "error":       None,
                    "error_type":  None,
                    "steps":       steps,
                    "duration_ms": round(elapsed * 1000, 1),
                },
                source="executor",
            )
        except Exception as ref_exc:
            logger.debug(
                "execution_complete (success) publish error (non-fatal): %s",
                ref_exc,
            )

        retry_manager.clear_task(task_id)
        logger.info("Task [%s] completed (method=%s)", task_id, method_used)

    # ─────────────────────────────────────────────────────────────────────
    # NEW: MethodDecision-driven execution (Plan §3.1, Gap 1, Gap 2, Gap 3)
    # ─────────────────────────────────────────────────────────────────────

    async def _execute_with_decision(
        self,
        task_id      : str,
        step_index   : int,
        step         : dict,
        context      : dict,
        decision     : MethodDecision,
    ) -> tuple[bool, Any, str]:
        """
        Execute a single step using the pre-computed MethodDecision.

        Walks the fallback_chain according to FailureClass rules:
          ENV_TRANSIENT    → retry same method ×MAX_RETRY_ATTEMPTS, no fallback
          ROUTING_MISMATCH → no retry, advance to next in fallback_chain,
                             publish routing_mismatch event for learner
          ENV_PERMANENT    → no retry, mark unavailable, advance fallback_chain
          EXECUTION_LOGIC  → no retry, no fallback, surface to debugger

        Gap 2: payload slot is read via decision.payload.for_method(method)
               — no translation occurs here.
        Gap 3: UIReadinessGuard.validate(decision) is called immediately
               before any UI tool invocation.
        """
        current = decision
        max_transient_retries: int = int(
            getattr(settings, "MAX_RETRY_ATTEMPTS", 3)
        )

        while True:
            method    = current.method
            tool_type = method.value   # "plugin" | "api" | "shell" | "ui"
            payload   = current.payload.for_method(method)

            bus.publish(
                "tool_selected",
                {
                    "task_id":    task_id,
                    "step_index": step_index,
                    "tool_type":  tool_type,
                    "method":     tool_type,
                },
                source="executor",
            )

            # Gap 3 — JIT UI readiness check immediately before invocation
            if method == MethodType.UI:
                try:
                    await asyncio.get_running_loop().run_in_executor(
                        None, _ui_readiness_guard.validate, current
                    )
                except FocusDriftError as drift_exc:
                    logger.warning(
                        "UIReadinessGuard: focus drift on step %d — "
                        "attempting re-focus: %s",
                        step_index, drift_exc,
                    )
                    # One re-focus attempt via focus_manager
                    refocused = False
                    if current.expected_app:
                        refocused = await focus_manager.ensure_focus(
                            current.expected_app
                        )
                    if not refocused:
                        stamped = current.with_failure(
                            FailureClass.ENV_TRANSIENT,
                            str(drift_exc),
                        )
                        return await self._handle_failure(
                            task_id, step_index, step, stamped,
                            max_transient_retries,
                        )
                    # Re-focus succeeded — re-run the JIT check once more
                    try:
                        await asyncio.get_running_loop().run_in_executor(
                            None, _ui_readiness_guard.validate, current
                        )
                    except (FocusDriftError, UIStateMismatchError) as recheck_exc:
                        stamped = current.with_failure(
                            FailureClass.ENV_TRANSIENT,
                            str(recheck_exc),
                        )
                        return await self._handle_failure(
                            task_id, step_index, step, stamped,
                            max_transient_retries,
                        )
                except UIStateMismatchError as mismatch_exc:
                    stamped = current.with_failure(
                        FailureClass.ENV_TRANSIENT,
                        str(mismatch_exc),
                    )
                    return await self._handle_failure(
                        task_id, step_index, step, stamped,
                        max_transient_retries,
                    )

            # ── Invoke the tool for this method ───────────────────────────
            transient_attempt = 0
            while True:
                try:
                    ok, result = await self._invoke_method(
                        method, payload, step, context, task_id
                    )
                    if ok:
                        return True, result, tool_type
                    # Tool ran but reported its own failure (e.g. plugin
                    # returned {"status": "error"}).  Classify and route.
                    _, failure_class = await error_classifier.classify_with_failure_class(
                        str(result)
                    )
                    break   # exit inner retry loop → handle_failure

                except Exception as exc:
                    _, failure_class = await error_classifier.from_exception(exc)
                    result = str(exc)

                    if failure_class == FailureClass.ENV_TRANSIENT:
                        transient_attempt += 1
                        if transient_attempt <= max_transient_retries:
                            backoff = 0.5 * (2 ** (transient_attempt - 1))
                            logger.warning(
                                "ENV_TRANSIENT on step %d attempt %d/%d "
                                "(method=%s) — retry in %.1fs: %s",
                                step_index, transient_attempt,
                                max_transient_retries, tool_type, backoff, exc,
                            )
                            await asyncio.sleep(backoff)
                            continue   # retry same method
                        # Retries exhausted — still transient; give up on
                        # this method without feeding the learner
                        logger.warning(
                            "ENV_TRANSIENT retries exhausted for method=%s "
                            "step=%d", tool_type, step_index,
                        )
                    break  # exit inner retry loop → handle_failure

            stamped = current.with_failure(failure_class, str(result))
            outcome = await self._handle_failure(
                task_id, step_index, step, stamped, max_transient_retries
            )

            # _handle_failure returns (False, error, method) for terminal
            # failures, or sets current to the next decision for fallback.
            if isinstance(outcome, tuple):
                return outcome

            # outcome is the next MethodDecision to try
            current = outcome

    async def _handle_failure(
        self,
        task_id              : str,
        step_index           : int,
        step                 : dict,
        decision             : MethodDecision,
        max_transient_retries: int,
    ) -> Any:
        """
        Apply Gap 1 failure routing rules and either:
          a) Return (False, error_dict, method_str) — terminal failure
          b) Return the next MethodDecision — continue with fallback

        Gap 1 rules:
          ENV_TRANSIENT    → terminal (retries already exhausted in caller)
          ROUTING_MISMATCH → publish routing_mismatch + advance fallback
          ENV_PERMANENT    → publish method_unavailable + advance fallback
          EXECUTION_LOGIC  → publish execution_error + terminal (surface bug)
        """
        failure_class  = decision.failure_class
        failure_detail = decision.failure_detail or "unknown error"
        method_str     = decision.method.value

        if failure_class == FailureClass.ENV_TRANSIENT:
            # Retries already exhausted — report failure, do NOT feed learner,
            # do NOT descend fallback chain (transient ≠ wrong method).
            logger.warning(
                "ENV_TRANSIENT exhausted on step %d method=%s: %s",
                step_index, method_str, failure_detail,
            )
            bus.publish(
                "execution_transient_failure",
                {
                    "task_id":      task_id,
                    "step_index":   step_index,
                    "method":       method_str,
                    "failure_class": failure_class.value,
                    "detail":       failure_detail,
                },
                source="executor",
            )
            return False, {"type": "transient", "message": failure_detail}, method_str

        if failure_class == FailureClass.ROUTING_MISMATCH:
            # Wrong method for this intent — feed the learner, try next fallback
            logger.warning(
                "ROUTING_MISMATCH on step %d method=%s: %s",
                step_index, method_str, failure_detail,
            )
            bus.publish(
                "routing_mismatch",
                {
                    "task_id":       task_id,
                    "step_index":    step_index,
                    "intent":        step.get("action"),
                    "method":        method_str,
                    "failure_class": failure_class.value,
                    "detail":        failure_detail,
                    "fallback_chain": [
                        m.value for m in decision.fallback_chain
                    ],
                    "decision_log":  decision.to_log_dict(),
                },
                source="executor",
            )

        elif failure_class == FailureClass.ENV_PERMANENT:
            logger.warning(
                "ENV_PERMANENT on step %d method=%s: %s",
                step_index, method_str, failure_detail,
            )
            bus.publish(
                "method_unavailable",
                {
                    "task_id":    task_id,
                    "method":     method_str,
                    "detail":     failure_detail,
                },
                source="executor",
            )

        elif failure_class == FailureClass.EXECUTION_LOGIC:
            logger.error(
                "EXECUTION_LOGIC bug on step %d method=%s: %s — "
                "surfacing to debugger, no fallback.",
                step_index, method_str, failure_detail,
            )
            bus.publish(
                "execution_logic_error",
                {
                    "task_id":    task_id,
                    "step_index": step_index,
                    "method":     method_str,
                    "detail":     failure_detail,
                },
                source="executor",
            )
            return (
                False,
                {"type": "logic_error", "message": failure_detail},
                method_str,
            )

        # Try next method in the fallback chain (ROUTING_MISMATCH + ENV_PERMANENT)
        next_method = decision.next_method()
        if next_method is None:
            logger.error(
                "Fallback chain exhausted after %s failure on step %d.",
                failure_class.name, step_index,
            )
            bus.publish(
                "fallback_exhausted",
                {
                    "task_id":       task_id,
                    "step_index":    step_index,
                    "failure_class": failure_class.value,
                    "detail":        failure_detail,
                },
                source="executor",
            )
            return (
                False,
                {"type": "exhausted", "message": failure_detail},
                method_str,
            )

        next_decision = decision.advance()
        logger.info(
            "Fallback: step %d advancing from %s → %s",
            step_index, method_str, next_decision.method.value,
        )
        bus.publish(
            "fallback_triggered",
            {
                "task_id":    task_id,
                "step_index": step_index,
                "from":       method_str,
                "to":         next_decision.method.value,
            },
            source="executor",
        )
        return next_decision   # caller continues the while loop

    async def _invoke_method(
        self,
        method  : MethodType,
        payload : Any,
        step    : dict,
        context : dict,
        task_id : str,
    ) -> tuple[bool, Any]:
        """
        Dispatch to the correct tool for *method* using the pre-serialized
        *payload* slot (Gap 2 — no translation here).

        Returns (ok: bool, result: Any).
        Raises on transient/permanent/logic errors — caller classifies.
        """
        action = step.get("action", "")

        if method == MethodType.PLUGIN:
            return await self._invoke_plugin(payload, action, context, task_id)

        if method == MethodType.API:
            return await self._invoke_api(payload, task_id)

        if method == MethodType.SHELL:
            return await self._invoke_shell(payload, step, context, task_id)

        if method == MethodType.UI:
            return await self._invoke_ui(payload, action, context, task_id)

        raise NotImplementedError(f"Unknown MethodType: {method}")

    async def _invoke_plugin(
        self,
        payload : Any,   # MappingProxyType (plugin_kwargs slot)
        action  : str,
        context : dict,
        task_id : str,
    ) -> tuple[bool, Any]:
        from plugins.registry import plugin_registry
        from brain.intent_matcher import match_intent_local

        kwargs = dict(payload) if payload else {}
        intent_str = kwargs.get("_intent") or action

        cap_map: dict[str, Any] = {}
        for pname, entry in plugin_registry.entries.items():
            for cap in getattr(entry.manifest, "capabilities", []) or []:
                cap_map[str(cap).lower().replace("_", " ")] = entry
            cap_map[pname.replace("_", " ")] = entry

        if not cap_map:
            return False, "No plugins registered"

        threshold = float(getattr(settings, "PLUGIN_EVOLVE_THRESHOLD", 0.75))
        matched_cap, score = match_intent_local(
            intent_str.lower().replace("_", " "),
            list(cap_map.keys()),
            threshold=threshold,
        )
        if not matched_cap:
            return False, f"No plugin matched intent '{intent_str}' above threshold {threshold}"

        entry           = cap_map[matched_cap]
        plugin_instance = entry.instance
        plugin_name     = entry.manifest.name

        plugin_args = self._normalize_args_for_plugin(
            plugin_instance, kwargs, action, context
        )
        validation_error = plugin_instance.validate(plugin_args)
        if validation_error:
            return False, f"Plugin '{plugin_name}' validation failed: {validation_error}"

        timeout = float(getattr(settings, "PLUGIN_TIMEOUT", 60.0))
        plugin_result = await asyncio.wait_for(
            plugin_instance.run(context, plugin_args),
            timeout=timeout,
        )

        if isinstance(plugin_result, dict):
            if plugin_result.get("status") == "success":
                return True, plugin_result.get("result")
            return False, plugin_result.get("message", "Plugin returned error")
        return True, plugin_result

    async def _invoke_api(
        self,
        payload : Any,   # MappingProxyType (api_body slot)
        task_id : str,
    ) -> tuple[bool, Any]:
        from tools.api_tool import api_tool
        args = dict(payload) if payload else {}
        result = await api_tool.run(args)
        if result.get("success"):
            return True, result.get("data")
        return False, result

    async def _invoke_shell(
        self,
        payload : Any,   # tuple[str, ...] (shell_argv slot)
        step    : dict,
        context : dict,
        task_id : str,
    ) -> tuple[bool, Any]:
        """
        Invoke the shell tool using the frozen argv tuple from LayeredPayload.
        Profile resolution (Bridge/Ghost/Lab) is preserved unchanged.
        """
        argv    = list(payload) if payload else []
        action  = step.get("action", "")
        args    = dict(step.get("args", {}))

        # Inject the reconstructed command into args so the shell tool's
        # existing logic (profile resolution, pts check) works unchanged.
        import shlex as _shlex
        args["command"] = _shlex.join(argv) if argv else args.get("command", "")
        args["task_id"] = task_id

        is_panel_sudo = (
            args.get("needs_password")
            or args.get("profile_hint") == "panel_sudo"
        )

        if not is_panel_sudo:
            args = await self._resolve_and_inject_profile(
                task_id, 0, action, args, context
            )
            if args is None:
                try:
                    chosen_profile = await asyncio.wait_for(
                        self._wait_for_target_selection(task_id), timeout=60.0
                    )
                except asyncio.TimeoutError:
                    return False, "Target selection timed out"
                args = dict(step.get("args", {}))
                args["task_id"]  = task_id
                args["_profile"] = chosen_profile
                args["cwd"]      = context.get("cwd")
        else:
            args["cwd"] = args.get("cwd") or context.get("cwd")

        tool_entry = tool_registry.get_entry("shell_tool")
        if not tool_entry:
            return False, "shell_tool not registered"
        tool = tool_entry.instance

        if asyncio.iscoroutinefunction(tool.run):
            ok, result = await tool.run(action, args)
        else:
            ok, result = await asyncio.get_running_loop().run_in_executor(
                None, tool.run, action, args
            )
        return ok, result

    async def _invoke_ui(
        self,
        payload : Any,   # MappingProxyType (ui_action slot)
        action  : str,
        context : dict,
        task_id : str,
    ) -> tuple[bool, Any]:
        """
        Invoke the UI tool.

        Note: capture_fresh_frame() must be called as the FIRST step inside
        the UI tool invocation itself (Optimization B), not here.  This
        method's sole responsibility is to dispatch the payload to the tool.
        """
        args = dict(payload) if payload else {}
        args["task_id"] = task_id

        tool_entry = tool_registry.get_entry("ui_tool")
        if not tool_entry:
            return False, "ui_tool not registered"
        tool = tool_entry.instance

        if asyncio.iscoroutinefunction(tool.run):
            ok, result = await tool.run(action, args)
        else:
            ok, result = await asyncio.get_running_loop().run_in_executor(
                None, tool.run, action, args
            )
        return ok, result

    # ── Legacy step execution (backward compat — capability waterfall) ─────

    async def _execute_step_safe(
        self,
        task_id: str,
        step_index: int,
        step: dict,
        context: dict,
        preferred_method: str | None = None,
    ) -> tuple[bool, Any, str]:
        """
        Legacy execution path for tasks that arrive without a MethodDecision.
        Preserved unchanged from the original so existing capabilities and
        tool routing continue to work during the migration period.
        """
        action = step.get("action")
        args   = step.get("args", {})

        if action in self.restricted_actions:
            return False, f"Restricted action blocked: {action}", "blocked"

        _SKIP_FOCUS_FOR_METHODS = {"plugin"}
        _needs_focus = (
            preferred_method not in _SKIP_FOCUS_FOR_METHODS
            and action not in _SHELL_ACTIONS
        )
        window_title = context.get("window_title")
        if window_title and _needs_focus:
            focused = await focus_manager.ensure_focus(window_title)
            if not focused:
                return (
                    False,
                    f"Failed to focus target window: {window_title}",
                    "focus_failed",
                )

        if action in _SHELL_ACTIONS:
            args = dict(args)
            args["task_id"] = task_id
            is_panel_sudo = (
                args.get("needs_password")
                or args.get("profile_hint") == "panel_sudo"
            )
            if not is_panel_sudo:
                args = await self._resolve_and_inject_profile(
                    task_id, step_index, action, args, context
                )
                if args is None:
                    try:
                        chosen_profile = await asyncio.wait_for(
                            self._wait_for_target_selection(task_id),
                            timeout=60.0,
                        )
                    except asyncio.TimeoutError:
                        return (
                            False,
                            "Target selection timed out — command cancelled.",
                            "target_selection",
                        )
                    args = dict(step.get("args", {}))
                    args["task_id"]  = task_id
                    args["_profile"] = chosen_profile
                    args["cwd"]      = context.get("cwd")
            else:
                args["cwd"] = args.get("cwd") or context.get("cwd")

        # ── Direct plugin dispatch ─────────────────────────────────────────
        try:
            from plugins.registry import plugin_registry
            from brain.intent_matcher import match_intent_local

            cap_map: dict[str, Any] = {}
            for pname, entry in plugin_registry.entries.items():
                caps = getattr(entry.manifest, "capabilities", []) or []
                for cap in caps:
                    cap_map[str(cap).lower().replace("_", " ")] = entry
                cap_map[pname.replace("_", " ")] = entry

            if cap_map:
                action_normalized = action.lower().replace("_", " ").strip()
                plugin_threshold  = float(
                    getattr(settings, "PLUGIN_INTENT_MATCH_THRESHOLD", 0.55)
                )
                matched_cap, score = match_intent_local(
                    action_normalized, list(cap_map.keys()),
                    threshold=plugin_threshold,
                )
                if matched_cap:
                    matched_entry   = cap_map[matched_cap]
                    plugin_instance = matched_entry.instance
                    plugin_name     = matched_entry.manifest.name

                    plugin_args = self._normalize_args_for_plugin(
                        plugin_instance, args or {}, action, context,
                    )
                    validation_error = plugin_instance.validate(plugin_args)
                    if validation_error:
                        logger.warning(
                            "Plugin '%s' validation still failed after "
                            "normalization: %s — falling through.",
                            plugin_name, validation_error,
                        )
                    else:
                        logger.info(
                            "Plugin dispatch: '%s' → '%s' (cap='%s', score=%.2f)",
                            action, plugin_name, matched_cap, score,
                        )
                        bus.publish(
                            "tool_selected",
                            {
                                "task_id":    task_id,
                                "step_index": step_index,
                                "tool_type":  "plugin",
                                "tool_name":  plugin_name,
                                "method":     "plugin",
                            },
                            source="executor",
                        )
                        try:
                            plugin_result = await asyncio.wait_for(
                                plugin_instance.run(context, plugin_args),
                                timeout=float(
                                    getattr(settings, "PLUGIN_TIMEOUT", 60.0)
                                ),
                            )
                            if isinstance(plugin_result, dict):
                                if plugin_result.get("status") == "success":
                                    return True, plugin_result.get("result"), "plugin"
                                logger.warning(
                                    "Plugin '%s' returned error: %s — falling through.",
                                    plugin_name,
                                    plugin_result.get("message", "unknown"),
                                )
                            else:
                                return True, plugin_result, "plugin"
                        except asyncio.TimeoutError:
                            logger.warning("Plugin '%s' timed out.", plugin_name)
                        except Exception as plugin_exc:
                            logger.warning(
                                "Plugin '%s' exception: %s — falling through.",
                                plugin_name, plugin_exc,
                            )
        except Exception as exc:
            logger.debug("Plugin dispatch pre-check failed (non-fatal): %s", exc)

        # ── Capability registry ────────────────────────────────────────────
        _NO_CAP_PREFIX  = "No capability registered"
        _cap_meta       = capability_registry.metadata.get(action, {})
        _cap_method     = _cap_meta.get("method") or "api"
        _cap_attempt    = 0
        _cap_max        = int(getattr(settings, "MAX_RETRY_ATTEMPTS", 3))
        last_error: Any = f"No tool available for action: {action}"
        method_used     = "unknown"

        while _cap_attempt <= _cap_max:
            try:
                cap_ok, cap_result = await capability_registry.execute(
                    action, context, args
                )
            except Exception as cap_exc:
                logger.debug(
                    "capability_registry.execute raised for '%s': %s",
                    action, cap_exc,
                )
                break

            if cap_ok:
                if isinstance(cap_result, dict) and "success" in cap_result:
                    if cap_result["success"]:
                        return True, cap_result.get("result"), _cap_method
                    return False, cap_result.get("result", "Capability failed"), _cap_method
                return True, cap_result, _cap_method

            cap_err_str = str(cap_result)
            if _NO_CAP_PREFIX in cap_err_str:
                break

            last_error = cap_result
            logger.warning(
                "Capability '%s' failed (attempt %d/%d): %s",
                action, _cap_attempt + 1, _cap_max, cap_result,
            )
            category = await self._classify_error_dynamically(str(last_error))
            if retry_manager.peek_should_retry(
                task_id, step_index, error_type=category, max_retries=_cap_max
            ):
                await retry_manager.should_retry(
                    task_id, step_index, error_type=category, max_retries=_cap_max
                )
                _cap_attempt += 1
                continue
            bus.publish(
                "retry_failed",
                {"task_id": task_id, "step": step_index},
                source="retry_manager",
            )
            return (
                False,
                {"type": "exhausted", "message": last_error, "tried": [action]},
                _cap_method,
            )

        # ── Tool waterfall ─────────────────────────────────────────────────
        waterfall         = self._build_waterfall(preferred_method)
        tried_tools: list[str] = []
        fallback_attempts = 0
        max_fallbacks     = int(getattr(settings, "MAX_RETRY_ATTEMPTS", 3))

        for tier in waterfall:
            if fallback_attempts >= max_fallbacks:
                break

            tool_type_hint = _TIER_TO_TOOL_TYPE.get(tier, tier)
            tool_type, tool_instance = await tool_selector.select_best_tool(
                {"intent": action, "method": tool_type_hint},
                context,
                exclude=tried_tools,
            )
            if not tool_instance:
                last_error = f"No {tier} tool registered for action '{action}'"
                continue

            tool_name = getattr(tool_instance, "name", tool_type)
            tried_tools.append(tool_name)
            method_used = tier

            bus.publish(
                "tool_selected",
                {
                    "task_id":    task_id,
                    "step_index": step_index,
                    "tool_type":  tool_type,
                    "tool_name":  tool_name,
                    "method":     tier,
                },
                source="executor",
            )

            try:
                import inspect as _inspect
                if not hasattr(tool_instance, "run"):
                    raise TypeError(
                        f"Tool '{tool_name}' has no run() method"
                    )
                _sig    = _inspect.signature(tool_instance.run)
                _params = list(_sig.parameters.keys())
                if len(_params) < 2:
                    raise TypeError(
                        f"Tool '{tool_name}'.run() unexpected signature {_params}"
                    )

                if asyncio.iscoroutinefunction(tool_instance.run):
                    ok, tool_result = await tool_instance.run(action, args)
                else:
                    ok, tool_result = await asyncio.get_running_loop().run_in_executor(
                        None, tool_instance.run, action, args
                    )

                if ok:
                    return True, tool_result, method_used
                last_error = tool_result

            except asyncio.TimeoutError:
                last_error = "Execution timed out"
            except TypeError as type_exc:
                last_error = str(type_exc)
                logger.error(
                    "Tool interface error for '%s' tier='%s': %s",
                    action, tier, type_exc,
                )
            except Exception as exc:
                last_error = str(exc)
                error_handler.handle_error(
                    exc, component="executor",
                    context={"task_id": task_id, "step": step_index},
                )

            category     = await self._classify_error_dynamically(str(last_error))
            should_retry = await retry_manager.should_retry(
                task_id, step_index, error_type=category
            )
            if should_retry:
                continue
            fallback_attempts += 1
            bus.publish(
                "fallback_triggered",
                {"from": tier, "task_id": task_id, "step_index": step_index},
                source="executor",
            )

        if method_used == "unknown" and not tried_tools:
            method_used = "none"

        return (
            False,
            {"type": "exhausted", "message": last_error, "tried": tried_tools},
            method_used,
        )

    # ── Shared helpers (unchanged from original) ───────────────────────────

    async def _classify_error_dynamically(self, result: Any) -> str:
        result_str = str(result).lower()
        patterns = {
            "permission_denied": r"(permission|denied|access|forbidden|not allowed)",
            "not_found":         r"(not found|no such|does not exist|404)",
            "timeout":           r"(timeout|timed out|deadline|took too long)",
            "network":           r"(connection|network|unreachable|offline)",
        }
        for category, pattern in patterns.items():
            if re.search(pattern, result_str):
                return category
        prompt = (
            f"Classify this execution error into one category.\n"
            f"Error: {result_str}\n"
            f'Return ONLY JSON: {{"category": "permission_denied|not_found|'
            f'timeout|network|unknown_error"}}'
        )
        try:
            response = await asyncio.wait_for(
                llm_client.generate(prompt, use_json=True), timeout=2.0
            )
            if isinstance(response, dict):
                return response.get("category", "unknown_error")
        except Exception:
            pass
        return "unknown_error"

    def _resolve_tool_call(
        self, intent: str, args: dict
    ) -> tuple[str, str, dict] | None:
        try:
            all_tools = tool_registry.get_all_tools()
        except Exception:
            all_tools = {}
        for tool_name, tool_obj in all_tools.items():
            if hasattr(tool_obj, "can_handle") and tool_obj.can_handle(intent):
                return tool_name, intent, args
            if (
                hasattr(tool_obj, "supported_intents")
                and intent in tool_obj.supported_intents
            ):
                return tool_name, intent, args
        return None

    async def _resolve_and_inject_profile(
        self,
        task_id    : str,
        step_index : int,
        action     : str,
        args       : dict,
        context    : dict,
    ) -> dict | None:
        cwd          = context.get("cwd") or args.get("cwd")
        command      = args.get("command") or args.get("cmd", "")
        profile_hint = args.get("profile_hint")
        venv_path    = args.get("venv_path") or context.get("venv_path")

        profile = await asyncio.get_running_loop().run_in_executor(
            None,
            terminal_resolver.resolve,
            cwd, command, profile_hint, venv_path,
        )
        logger.info(
            "Task [%s] step %d → %s", task_id, step_index, type(profile).__name__
        )

        if isinstance(profile, BridgeTarget):
            profile = await asyncio.get_running_loop().run_in_executor(
                None, self._check_bridge_permission, profile, task_id, cwd,
            )

        if isinstance(profile, AmbiguousTarget):
            bus.publish(
                "target_selection_required",
                {
                    "task_id":    task_id,
                    "step_index": step_index,
                    "command":    command,
                    "candidates": [
                        {
                            "pts_path":     c.pts_path,
                            "window_id":    c.window_id,
                            "window_title": c.window_title,
                            "cwd":          c.cwd,
                        }
                        for c in profile.candidates
                    ],
                },
                source="executor",
            )
            return None

        enriched = dict(args)
        enriched["_profile"] = profile
        enriched["cwd"]      = cwd
        return enriched

    @staticmethod
    def _check_bridge_permission(
        profile: BridgeTarget,
        task_id: str,
        cwd    : str | None,
    ) -> BridgeTarget | GhostTarget:
        pts = profile.pts_path
        if not pts or not os.path.exists(pts):
            logger.warning(
                "Bridge pre-flight: pts '%s' missing — downgrading to Ghost", pts
            )
            bus.publish(
                "bridge_permission_denied",
                {
                    "task_id":       task_id,
                    "pts":           pts,
                    "reason":        "pts device not found",
                    "fallback":      "ghost",
                    "action_needed": "Check that the target terminal is still open.",
                },
                source="executor",
            )
            return GhostTarget(cwd=cwd)

        try:
            fd = os.open(pts, os.O_WRONLY | os.O_NOCTTY)
            os.close(fd)
            logger.debug("Bridge pre-flight: pts '%s' writable ✓", pts)
            return profile
        except PermissionError:
            logger.warning(
                "Bridge pre-flight: PermissionError on '%s' — downgrading to Ghost.",
                pts,
            )
            bus.publish(
                "bridge_permission_denied",
                {
                    "task_id":       task_id,
                    "pts":           pts,
                    "reason":        "ptrace_scope hardening blocks pts write",
                    "fallback":      "ghost",
                    "action_needed": (
                        "Run setup.sh OR manually: "
                        "echo 0 | sudo tee /proc/sys/kernel/yama/ptrace_scope"
                    ),
                },
                source="executor",
            )
            return GhostTarget(cwd=cwd)
        except OSError as exc:
            logger.warning(
                "Bridge pre-flight: OSError on '%s': %s — Ghost", pts, exc
            )
            bus.publish(
                "bridge_permission_denied",
                {
                    "task_id":  task_id,
                    "pts":      pts,
                    "reason":   f"OS error: {exc}",
                    "fallback": "ghost",
                },
                source="executor",
            )
            return GhostTarget(cwd=cwd)

    async def _wait_for_target_selection(
        self, task_id: str
    ) -> BridgeTarget:
        loop: asyncio.AbstractEventLoop = asyncio.get_running_loop()
        fut: asyncio.Future = loop.create_future()
        self._pending_target_selections[task_id] = fut
        return await fut

    async def _on_target_selected(self, event: Any) -> None:
        data    = event.data if hasattr(event, "data") else event
        task_id = data.get("task_id")
        fut     = self._pending_target_selections.pop(task_id, None)
        if fut is None or fut.done():
            logger.warning("target_selected for unknown task: %s", task_id)
            return
        chosen = BridgeTarget(
            pts_path     = data.get("pts_path", ""),
            window_id    = data.get("window_id", ""),
            window_title = data.get("window_title", ""),
            cwd          = data.get("cwd"),
        )
        fut.set_result(chosen)
        logger.info(
            "Target selected [%s]: %s (%s)",
            task_id, chosen.window_title, chosen.pts_path,
        )

    @staticmethod
    def _enrich_context_with_cwd(context: dict) -> None:
        if context.get("cwd"):
            return
        if context.get("window_cwd"):
            context["cwd"] = context["window_cwd"]
            return
        app_ctx = context.get("app_context") or {}
        if app_ctx.get("cwd"):
            context["cwd"] = app_ctx["cwd"]
            return
        context["cwd"] = os.getcwd()
        logger.debug("cwd not in context — using process CWD: %s", context["cwd"])

    @staticmethod
    def _build_waterfall(preferred_method: str | None) -> list[str]:
        if preferred_method and preferred_method in _WATERFALL_ORDER:
            idx = _WATERFALL_ORDER.index(preferred_method)
            return _WATERFALL_ORDER[idx:] + _WATERFALL_ORDER[:idx]
        return list(_WATERFALL_ORDER)

    @staticmethod
    def _normalize_args_for_plugin(
        plugin_instance: Any,
        args           : dict,
        action         : str,
        context        : dict,
    ) -> dict:
        _SHELL_VERBS: frozenset[str] = frozenset({
            "open", "run", "execute", "launch", "start",
            "xdg-open", "gtk-launch", "gio",
        })
        _INTERNAL_KEYS: frozenset[str] = frozenset({
            "_profile", "task_id", "cwd", "profile_hint"
        })

        first_error = plugin_instance.validate(args)
        if first_error is None:
            return args

        normalized = {k: v for k, v in args.items() if k in _INTERNAL_KEYS}
        positional: list[str] = [str(a) for a in (args.get("args") or [])]
        command: str          = str(args.get("command") or "").strip().lower()
        target_val: str | None = (
            positional[0] if positional else
            args.get("target") or args.get("name") or None
        )
        raw_scalars: dict[str, Any] = {
            k: v for k, v in args.items()
            if k not in _INTERNAL_KEYS and k not in {"args", "command"}
        }
        candidate: dict[str, Any] = dict(raw_scalars)

        if "app_name" not in candidate:
            if target_val and command in _SHELL_VERBS:
                candidate["app_name"] = target_val
            elif command and command not in _SHELL_VERBS:
                candidate["app_name"] = command
            elif target_val:
                candidate["app_name"] = target_val

        for fkey in ("file_path", "path"):
            if fkey not in candidate and target_val:
                candidate[fkey] = target_val

        if "url" not in candidate and target_val:
            candidate["url"] = target_val

        if "query" not in candidate:
            candidate["query"] = args.get("text") or target_val or action

        if "text" not in candidate:
            candidate["text"] = args.get("query") or target_val or action

        test_args       = {**normalized, **candidate}
        remaining_error = plugin_instance.validate(test_args)
        if remaining_error and positional:
            import re as _re
            missing_keys = _re.findall(r"['\"](\w+)['\"]", remaining_error)
            for i, mkey in enumerate(missing_keys):
                if mkey not in candidate and i < len(positional):
                    candidate[mkey] = positional[i]

        normalized.update(candidate)

        final_error = plugin_instance.validate(normalized)
        if final_error:
            logger.debug(
                "_normalize_args_for_plugin: still invalid after normalization: %s "
                "(action=%s)", final_error, action,
            )
        return normalized


executor = Executor()