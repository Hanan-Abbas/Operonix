"""
executor/executor.py

Panel integration
─────────────────
• Reads `preferred_method` from the incoming execution payload.
  If set (panel strategy override), the waterfall skips straight to that
  tier before falling back to lower tiers on failure.
• Emits `task_completed` with `method_used` and `intent` so the orchestrator
  can relay them to the panel's history row via `action_completed`.
"""
from __future__ import annotations

import asyncio
import logging
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


class Executor:

    def __init__(self) -> None:
        self.os_name = platform.system()
        self.is_running = False
        self.restricted_actions: set[str] = set()

    async def start(self) -> None:
        bus.subscribe("task_safety_cleared", self.execute_plan)
        self.is_running = True
        logger.info("⚙️ Executor Online | OS: %s", self.os_name)
        logger.info("⚙️ Tools Loaded: %d", len(tool_registry.list_tools()))

    # ── Main entry point ──────────────────────────────────────────────────────

    async def execute_plan(self, event: Any) -> None:
        metrics.total_tasks += 1
        start = time.time()

        task_data = event.data
        task_id = task_data.get("task_id")
        steps = task_data.get("steps", [])
        context = task_data.get("context", {})
        intent = task_data.get("intent")

        # Panel strategy override: if a preferred_method was chosen by the
        # user in the suggestion list, honour it as the starting tier.
        preferred_method: str | None = task_data.get("preferred_method")

        logger.info(
            "🚀 Starting Task [%s] with %d steps (preferred_method=%s)",
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
                        "task_id": task_id,
                        "failed_step": step,
                        "error": result,
                        "intent": intent,
                        "method_used": method_used,
                    },
                    source="executor",
                )
                retry_manager.clear_task(task_id)
                logger.error("❌ Task [%s] failed at step %d: %s", task_id, step_index, result)
                return

            context["last_result"] = result
            context["last_action"] = action

            bus.publish(
                "execution_step_success",
                {"task_id": task_id, "step_index": step_index, "result": result},
                source="executor",
            )
            logger.info("✅ Step %d completed: %s", step_index, action)

        elapsed = time.time() - start
        metrics.total_duration_seconds += elapsed
        metrics.successful_tasks += 1

        logger.info(
            "📊 Success rate: %.1f%% | Avg duration: %.2fs",
            metrics.success_rate(), metrics.avg_task_duration(),
        )

        # Include method_used and intent so orchestrator.finalize_task can
        # forward them to the panel via action_completed.
        bus.publish(
            "task_completed",
            {
                "task_id": task_id,
                "intent": intent,
                "steps": steps,
                "method_used": method_used,
            },
            source="executor",
        )

        retry_manager.clear_task(task_id)
        logger.info("🏁 Task [%s] completed successfully (method=%s)", task_id, method_used)

    # ── Step execution ────────────────────────────────────────────────────────

    async def _execute_step_safe(
        self,
        task_id: str,
        step_index: int,
        step: dict,
        context: dict,
        preferred_method: str | None = None,
    ) -> tuple[bool, Any, str]:
        """
        Execute one step, respecting the panel's preferred_method if set.

        Returns (success, result, method_used_str).
        """
        action = step.get("action")
        args = step.get("args", {})

        if action in self.restricted_actions:
            return False, f"Restricted action blocked: {action}", "blocked"

        window_title = context.get("window_title")
        if window_title:
            focused = await focus_manager.ensure_focus(window_title)
            if not focused:
                return False, f"Failed to focus target window: {window_title}", "focus_failed"

        # Build the waterfall starting from the preferred tier (panel override).
        waterfall = self._build_waterfall(preferred_method)

        tried_tools: list[str] = []
        fallback_attempts = 0
        max_fallbacks = int(getattr(settings, "MAX_RETRY_ATTEMPTS", 3))
        last_error: Any = f"No tool available for action: {action}"
        method_used = "unknown"

        for tier in waterfall:
            if fallback_attempts >= max_fallbacks:
                break

            tool_type, tool_instance = await tool_selector.select_best_tool(
                {"intent": action, "method": tier},
                context,
                exclude=tried_tools,
            )

            if not tool_instance:
                logger.debug("No %s tool available for '%s', trying next tier.", tier, action)
                continue

            tool_name = getattr(tool_instance, "name", tool_type)
            tried_tools.append(tool_name)
            method_used = tier

            bus.publish(
                "tool_selected",
                {
                    "task_id": task_id,
                    "step_index": step_index,
                    "tool_type": tool_type,
                    "tool_name": tool_name,
                    "method": tier,
                },
                source="executor",
            )

            try:
                success, result = await capability_registry.execute(action, context, args)

                if success:
                    # Resolve the capability result to a concrete tool call.
                    action_data = result if isinstance(result, dict) else {}
                    cap_intent = action_data.get("intent") or action
                    cap_args = action_data.get("args") or args
                    resolved = self._resolve_tool_call(cap_intent, cap_args)

                    if resolved:
                        tool_name_r, tool_action, tool_args = resolved
                        tool = tool_registry.get_tool(tool_name_r)
                        if tool:
                            ok, tool_result = await tool.run(tool_action, tool_args)
                            if ok:
                                logger.debug(
                                    "Action '%s' → %s.%s OK", action, tool_name_r, tool_action
                                )
                                return True, tool_result, method_used
                            last_error = tool_result
                        else:
                            last_error = f"Tool not registered: {tool_name_r}"
                    else:
                        # Capability returned success but no tool mapping — treat as ok.
                        return True, result, method_used
                else:
                    last_error = result

            except asyncio.TimeoutError:
                last_error = "Execution timed out"
            except Exception as exc:
                last_error = str(exc)
                error_handler.handle_error(
                    exc,
                    component="executor",
                    context={"task_id": task_id, "step": step_index},
                )

            # Classify the error and decide whether to retry within this tier.
            error_str = str(last_error)
            category = await self._classify_error_dynamically(error_str)

            should_retry = await retry_manager.should_retry(
                task_id, step_index, error_type=category
            )
            if should_retry:
                logger.info("Retrying step %d (error=%s)", step_index, category)
                # Stay on the same tier for the retry.
                continue

            # Move to the next tier in the waterfall.
            fallback_attempts += 1
            bus.publish(
                "fallback_triggered",
                {"from": tier, "task_id": task_id, "step_index": step_index},
                source="executor",
            )

        return False, {"type": "exhausted", "message": last_error, "tried": tried_tools}, method_used

    # ── Waterfall helpers ─────────────────────────────────────────────────────

    @staticmethod
    def _build_waterfall(preferred_method: str | None) -> list[str]:
        """
        Return the execution tier order, starting from preferred_method if set.
        Falls back to the full waterfall so no method is permanently skipped.
        """
        if preferred_method and preferred_method in _WATERFALL_ORDER:
            idx = _WATERFALL_ORDER.index(preferred_method)
            # Start from the preferred tier, then wrap to earlier tiers as fallback.
            return _WATERFALL_ORDER[idx:] + _WATERFALL_ORDER[:idx]
        return list(_WATERFALL_ORDER)

    # ── Error classification ──────────────────────────────────────────────────

    async def _classify_error_dynamically(self, result: Any) -> str:
        result_str = str(result).lower()

        fallback_patterns = {
            "permission_denied": r"(permission|denied|access|forbidden|not allowed)",
            "not_found":         r"(not found|no such|does not exist|404)",
            "timeout":           r"(timeout|timed out|deadline|took too long)",
            "network":           r"(connection|network|unreachable|offline)",
        }
        for category, pattern in fallback_patterns.items():
            if re.search(pattern, result_str):
                logger.info("Error classified as '%s' (regex)", category)
                return category

        prompt = f"""
Classify this execution error into one category.
Error: {result_str}
Return ONLY JSON: {{"category": "permission_denied|not_found|timeout|network|unknown_error"}}
"""
        try:
            response = await asyncio.wait_for(
                llm_client.generate(prompt, use_json=True), timeout=2.0
            )
            if isinstance(response, dict):
                return response.get("category", "unknown_error")
            return "unknown_error"
        except Exception:  # noqa: BLE001
            return "unknown_error"

    # ── Tool resolution ───────────────────────────────────────────────────────

    def _resolve_tool_call(
        self, intent: str, args: dict
    ) -> tuple[str, str, dict] | None:
        """Maps an abstract intent to a registered concrete tool."""
        for tool_name, tool_obj in tool_registry.list_tools().items():
            if hasattr(tool_obj, "can_handle") and tool_obj.can_handle(intent):
                return tool_name, intent, args
        return None


executor = Executor()