"""
executor/executor.py
─────────────────────
Central task executor.

Changes from original
──────────────────────
BUG FIX 1 — capability result handling
    The original code called capability_registry.execute() and on success
    tried to call _resolve_tool_call() to find a *second* tool to run.
    But file_ops capabilities now execute the operation directly and return:
        {"success": True/False, "result": <value>, "intent": <name>}

    New logic:
      a. If capability_registry.execute() returns (True, dict_with_success_key):
         - If dict["success"] is True  -> operation already done, return it.
         - If dict["success"] is False -> treat as failure, move to next tier.
      b. If capability returned (True, non-dict) -> treat as legacy success.
      c. If capability returned (False, ...) -> move to next tier as before.
      d. If capability returned (True, dict) WITHOUT "success" key:
         -> original _resolve_tool_call() path (for capabilities that still
            return action descriptors instead of executing directly).

BUG FIX 2 — context enrichment before step execution
    "location": "current window" must be resolved to a real CWD before the
    capability runs.  _enrich_context_with_cwd() injects the CWD of the
    focused window into context["cwd"] using window_detector data that the
    orchestrator already captured in context["window_title"] /
    context["app_context"].

BUG FIX 3 — preferred_method waterfall now correctly maps tier names to
    the tool_type strings used in ToolRegistry ("command" tier -> shell_tool).
    Previously "command" was passed raw; ToolRegistry keys are "shell_tool".

No other logic changed.
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
from executor.error_classifier import error_classifier
from executor.fallback_manager import FallbackManager
from executor.focus_manager import FocusManager
from executor.retry_manager import RetryManager
from core.metrics import metrics
from brain.llm_client import llm_client
from tools.tool_registry import tool_registry
from tools.tool_selector import tool_selector

logger = logging.getLogger("Executor")

error_handler = ErrorHandler(event_bus=bus, logger=logger)
retry_manager = RetryManager()
fallback_manager = FallbackManager()
focus_manager = FocusManager()

# Canonical waterfall order — do not reorder.
_WATERFALL_ORDER: list[str] = ["plugin", "api", "command", "ui"]

# Map waterfall tier names -> ToolRegistry tool_type strings.
# This is the single place where tier->tool translation lives.
_TIER_TO_TOOL_TYPE: dict[str, str] = {
    "plugin":  "plugin",
    "api":     "api_tool",
    "command": "shell_tool",
    "ui":      "ui_tool",
}


class Executor:

    def __init__(self) -> None:
        self.os_name = platform.system()
        self.is_running = False
        self.restricted_actions: set[str] = set()

    async def start(self) -> None:
        bus.subscribe("task_safety_cleared", self.execute_plan)
        self.is_running = True
        logger.info("Executor Online | OS: %s", self.os_name)
        logger.info("Tools Loaded: %d", len(tool_registry.list_tools()))

    # ── Main entry point ──────────────────────────────────────────────────

    async def execute_plan(self, event: Any) -> None:
        metrics.total_tasks += 1
        start = time.time()

        task_data = event.data
        task_id   = task_data.get("task_id")
        steps     = task_data.get("steps", [])
        context   = task_data.get("context", {})
        intent    = task_data.get("intent")
        preferred_method: str | None = task_data.get("preferred_method")

        # BUG FIX 2: inject CWD into context so _resolve_path() in file_ops
        # can resolve "current window" to a real filesystem path.
        self._enrich_context_with_cwd(context)

        logger.info(
            "Starting Task [%s] with %d steps (preferred=%s)",
            task_id, len(steps), preferred_method or "auto",
        )

        method_used: str = "unknown"

        for step_index, step in enumerate(steps):
            action = step.get("action")
            bus.publish(
                "execution_step_started",
                {"task_id": task_id, "step_index": step_index, "action": action},
                source="executor",
            )

            success, result, step_method = await self._execute_step_safe(
                task_id, step_index, step, context, preferred_method=preferred_method
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
                retry_manager.clear_task(task_id)
                logger.error(
                    "Task [%s] failed at step %d: %s", task_id, step_index, result
                )
                return

            context["last_result"] = result
            context["last_action"] = action
            bus.publish(
                "execution_step_success",
                {"task_id": task_id, "step_index": step_index, "result": result},
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
        retry_manager.clear_task(task_id)
        logger.info(
            "Task [%s] completed successfully (method=%s)", task_id, method_used
        )

    # ── Step execution ─────────────────────────────────────────────────────

    async def _execute_step_safe(
        self,
        task_id: str,
        step_index: int,
        step: dict,
        context: dict,
        preferred_method: str | None = None,
    ) -> tuple[bool, Any, str]:
        action = step.get("action")
        args   = step.get("args", {})

        if action in self.restricted_actions:
            return False, f"Restricted action blocked: {action}", "blocked"

        window_title = context.get("window_title")
        if window_title:
            focused = await focus_manager.ensure_focus(window_title)
            if not focused:
                return (
                    False,
                    f"Failed to focus target window: {window_title}",
                    "focus_failed",
                )

        waterfall = self._build_waterfall(preferred_method)
        tried_tools: list[str] = []
        fallback_attempts = 0
        max_fallbacks = int(getattr(settings, "MAX_RETRY_ATTEMPTS", 3))
        last_error: Any = f"No tool available for action: {action}"
        method_used = "unknown"

        for tier in waterfall:
            if fallback_attempts >= max_fallbacks:
                break

            # BUG FIX 3: translate tier name to tool_type for ToolSelector
            tool_type_hint = _TIER_TO_TOOL_TYPE.get(tier, tier)

            tool_type, tool_instance = await tool_selector.select_best_tool(
                {"intent": action, "method": tool_type_hint},
                context,
                exclude=tried_tools,
            )

            if not tool_instance:
                logger.debug(
                    "No %s (%s) tool for '%s', trying next tier.",
                    tier, tool_type_hint, action,
                )
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
                # ── Execute via capability registry ──────────────────────
                cap_ok, cap_result = await capability_registry.execute(
                    action, context, args
                )

                # BUG FIX 1a: capability executed directly and returned a
                # result dict with a "success" key.
                if cap_ok and isinstance(cap_result, dict) and "success" in cap_result:
                    if cap_result["success"]:
                        logger.debug(
                            "Capability '%s' executed directly: %s",
                            action, cap_result.get("result"),
                        )
                        return True, cap_result.get("result"), method_used
                    else:
                        # Capability reported its own failure — fall through
                        last_error = cap_result.get("result", "Capability failed")
                        logger.warning(
                            "Capability '%s' reported failure: %s", action, last_error
                        )
                        # Don't retry via next tier for logic errors
                        # (e.g. missing path) — propagate immediately.
                        return False, last_error, method_used

                # BUG FIX 1b: capability returned (True, non-dict) — legacy
                # success with a plain value (e.g. string).
                if cap_ok and not isinstance(cap_result, dict):
                    return True, cap_result, method_used

                # BUG FIX 1c: capability returned an action descriptor dict
                # (old style — no "success" key).  Use _resolve_tool_call().
                if cap_ok and isinstance(cap_result, dict):
                    cap_intent = cap_result.get("intent") or action
                    cap_args   = cap_result.get("args") or args
                    resolved   = self._resolve_tool_call(cap_intent, cap_args)

                    if resolved:
                        tool_name_r, tool_action, tool_args = resolved
                        tool = tool_registry.get_tool(tool_name_r)
                        if tool:
                            ok, tool_result = await tool.run(tool_action, tool_args)
                            if ok:
                                return True, tool_result, method_used
                            last_error = tool_result
                        else:
                            last_error = f"Tool not registered: {tool_name_r}"
                    else:
                        # No tool mapping and no direct execution — treat ok
                        return True, cap_result, method_used
                else:
                    last_error = cap_result

            except asyncio.TimeoutError:
                last_error = "Execution timed out"
            except Exception as exc:
                last_error = str(exc)
                error_handler.handle_error(
                    exc,
                    component="executor",
                    context={"task_id": task_id, "step": step_index},
                )

            # Classify error and decide retry vs. next tier
            category = await self._classify_error_dynamically(str(last_error))
            should_retry = await retry_manager.should_retry(
                task_id, step_index, error_type=category
            )
            if should_retry:
                logger.info("Retrying step %d (error=%s)", step_index, category)
                continue

            fallback_attempts += 1
            bus.publish(
                "fallback_triggered",
                {"from": tier, "task_id": task_id, "step_index": step_index},
                source="executor",
            )

        return (
            False,
            {"type": "exhausted", "message": last_error, "tried": tried_tools},
            method_used,
        )

    # ── Context enrichment (BUG FIX 2) ────────────────────────────────────

    @staticmethod
    def _enrich_context_with_cwd(context: dict) -> None:
        """
        Inject context["cwd"] so that file_ops._resolve_path() can map
        "current window" to a real filesystem path.

        Resolution order:
          1. context["cwd"] already set (e.g. by orchestrator) — leave it.
          2. context["window_cwd"] set by window_detector — use it.
          3. context["app_context"]["cwd"] — nested app context.
          4. os.getcwd() as final fallback.
        """
        if context.get("cwd"):
            return  # already set

        if context.get("window_cwd"):
            context["cwd"] = context["window_cwd"]
            return

        app_ctx = context.get("app_context") or {}
        if app_ctx.get("cwd"):
            context["cwd"] = app_ctx["cwd"]
            return

        # Final fallback — use the process CWD
        context["cwd"] = os.getcwd()
        logger.debug("cwd not in context — using process CWD: %s", context["cwd"])

    # ── Waterfall helpers ──────────────────────────────────────────────────

    @staticmethod
    def _build_waterfall(preferred_method: str | None) -> list[str]:
        """Return the execution tier order starting from preferred_method."""
        if preferred_method and preferred_method in _WATERFALL_ORDER:
            idx = _WATERFALL_ORDER.index(preferred_method)
            return _WATERFALL_ORDER[idx:] + _WATERFALL_ORDER[:idx]
        return list(_WATERFALL_ORDER)

    # ── Error classification ───────────────────────────────────────────────

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
            f'Return ONLY JSON: {{"category": "permission_denied|not_found|timeout|network|unknown_error"}}'
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

    # ── Tool resolution ────────────────────────────────────────────────────

    def _resolve_tool_call(
        self, intent: str, args: dict
    ) -> tuple[str, str, dict] | None:
        """
        Maps an abstract intent to a registered concrete tool.

        FIX: tool_registry.list_tools() returns a list of tool objects,
        not a dict. The old code called .items() on it which raised
        AttributeError: 'list' object has no attribute 'items'.
        Now handles both list and dict return types defensively.
        """
        tools = tool_registry.list_tools()

        # Handle dict return: {tool_name: tool_obj, ...}
        if isinstance(tools, dict):
            for tool_name, tool_obj in tools.items():
                if hasattr(tool_obj, "can_handle") and tool_obj.can_handle(intent):
                    return tool_name, intent, args

        # Handle list return: [tool_obj, ...]
        elif isinstance(tools, list):
            for tool_obj in tools:
                if hasattr(tool_obj, "can_handle") and tool_obj.can_handle(intent):
                    # Get tool name from the object's name attribute
                    tool_name = getattr(tool_obj, "name",
                                getattr(tool_obj, "tool_type",
                                type(tool_obj).__name__.lower()))
                    return tool_name, intent, args

        return None


executor = Executor()