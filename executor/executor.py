# executor/executor.py
import asyncio
import platform
import os
import logging

from core.event_bus import bus
from tools.tool_registry import tool_registry
from tools.tool_selector import tool_selector
from capabilities.registry import capability_registry
from context.context_validator import context_validator

from executor.retry_manager import RetryManager
from executor.fallback_manager import FallbackManager
from executor.focus_manager import FocusManager

logger = logging.getLogger("Executor")

# -------------------------
# Global Managers
# -------------------------
retry_manager = RetryManager()
fallback_manager = FallbackManager()
focus_manager = FocusManager()


class Executor:
    def __init__(self):
        self.os_name = platform.system()
        self.is_running = False

        # Safety restrictions
        self.restricted_actions = {"delete_file", "run_shell"}

    # -------------------------
    # Start Executor
    # -------------------------
    async def start(self):
        bus.subscribe("plan_ready", self.execute_plan)
        self.is_running = True
        logger.info(f"⚙️ Executor Online | OS: {self.os_name}")
        logger.info(f"⚙️ Tools Loaded: {len(tool_registry.list_tools())}")

    # -------------------------
    # Main Execution Loop
    # -------------------------
    async def execute_plan(self, event):
        task_id = event.data.get("task_id")
        steps = event.data.get("steps", [])
        context = event.data.get("context", {})

        logger.info(f"🚀 Starting Task [{task_id}] with {len(steps)} steps")

        for step_index, step in enumerate(steps):
            action = step.get("action")

            await bus.emit("execution_step_started", {
                "task_id": task_id,
                "step_index": step_index,
                "action": action
            })

            # -------------------------
            # Execute step safely
            # -------------------------
            success, result = await self._execute_step_safe(
                task_id, step_index, step, context
            )

            if not success:
                await bus.emit("task_failed", {
                    "task_id": task_id,
                    "failed_step": step,
                    "error": result
                })
                retry_manager.clear_task(task_id)
                return

            # Update context for next step
            context["last_result"] = result
            context["last_action"] = action

            await bus.emit("execution_step_success", {
                "task_id": task_id,
                "step_index": step_index,
                "result": result
            })

        await bus.emit("task_completed", {"task_id": task_id})
        retry_manager.clear_task(task_id)

    # -------------------------
    # Step Execution with Resilience
    # -------------------------
    async def _execute_step_safe(self, task_id, step_index, step, context):
        action = step.get("action")
        args = step.get("args", {})

        # Normalize paths
        if "path" in args:
            args["path"] = os.path.normpath(args["path"])

        # Restricted actions
        if action in self.restricted_actions:
            return False, f"Restricted action blocked: {action}"

        # Context validation
        valid, reason = await context_validator.validate_action_context(action, context)
        if not valid:
            return False, f"Context validation failed: {reason}"

        # Focus handling
        if context.get("window_title"):
            focused = await focus_manager.ensure_focus(context["window_title"])
            if not focused:
                return False, "Failed to focus target window"

        tried_tools = []
        fallback_attempts = 0
        max_fallbacks = 5

        while fallback_attempts < max_fallbacks:
            # -------------------------
            # Select best tool/plugin
            # -------------------------
            tool_type, tool_instance = await tool_selector.select_best_tool(
                {"intent": action},
                context,
                exclude=tried_tools
            )

            if not tool_instance:
                return False, f"No tool/plugin available for action: {action}"

            tried_tools.append(getattr(tool_instance, "name", tool_type))

            await bus.emit("tool_selected", {
                "task_id": task_id,
                "step_index": step_index,
                "tool_type": tool_type,
                "tool_name": getattr(tool_instance, "name", tool_type)
            })

            try:
                # -------------------------
                # Execute capability
                # -------------------------
                success, result = await capability_registry.execute(action, context, args)

                await bus.emit("execution_strategy_used", {
                    "task_id": task_id,
                    "step_index": step_index,
                    "tool_type": tool_type,
                    "tool_name": getattr(tool_instance, "name", tool_type)
                })

                if success:
                    return True, result

                error_type = self._classify_error(result)

            except asyncio.TimeoutError:
                error_type = "timeout"
                result = "Execution timed out"

            except Exception as e:
                error_type = "exception"
                result = str(e)

            # -------------------------
            # Retry
            # -------------------------
            should_retry = await retry_manager.should_retry(
                task_id, step_index, error_type=error_type
            )
            if should_retry:
                continue

            # -------------------------
            # Fallback
            # -------------------------
            next_tool_type = fallback_manager.get_fallback(tool_type)
            if next_tool_type:
                await bus.emit("fallback_triggered", {
                    "from": tool_type,
                    "to": next_tool_type,
                    "task_id": task_id,
                    "step_index": step_index
                })
                fallback_attempts += 1
                continue

            return False, {
                "type": error_type,
                "message": result,
                "tried_tools": tried_tools
            }

        return False, f"Max fallback attempts reached for action '{action}'"

    # -------------------------
    # Helpers
    # -------------------------
    def _classify_error(self, result):
        text = str(result).lower()
        if "permission" in text:
            return "permission_denied"
        if "not found" in text:
            return "not_found"
        if "timeout" in text:
            return "timeout"
        return "unknown_error"


# -------------------------
# Global Executor instance
# -------------------------
executor = Executor()
