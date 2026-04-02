import asyncio
import logging
import os
import platform

from capabilities.registry import capability_registry
from context.context_validator import context_validator
# --- IMPORT CONFIG AND ERROR HANDLER ---
from core.config import settings
from core.error_handler import ErrorHandler
from core.event_bus import bus
from executor.fallback_manager import FallbackManager
from executor.focus_manager import FocusManager
from executor.retry_manager import RetryManager
from tools.tool_registry import tool_registry
from tools.tool_selector import tool_selector

logger = logging.getLogger("Executor")

# Initialize error handler for the execution layer
error_handler = ErrorHandler(event_bus=bus, logger=logger)

# -------------------------
# Global Managers
# -------------------------
retry_manager = RetryManager()
fallback_manager = FallbackManager()
focus_manager = FocusManager()


class Executor:
    """⚙️ Central Execution Layer

    - Executes plans step by step
    - Validates context
    - Chooses best tool
    - Handles retries and fallback
    - Emits events to bus
    """

    def __init__(self):
        self.os_name = platform.system()
        self.is_running = False
        self.restricted_actions = set()

    # -------------------------
    # Start Executor
    # -------------------------
    async def start(self):
        # 🔄 CHANGE 1: Listen ONLY to tasks that have passed the Safety Validator!
        bus.subscribe("task_safety_cleared", self.execute_plan)

        self.is_running = True
        logger.info(f"⚙️ Executor Online | OS: {self.os_name}")
        logger.info(f"⚙️ Tools Loaded: {len(tool_registry.list_tools())}")

    # -------------------------
    # Main Execution Loop
    # -------------------------
    async def execute_plan(self, event):
        task_data = event.data
        task_id = task_data.get("task_id")
        steps = task_data.get("steps", [])
        context = task_data.get("context", {})

        # 🔄 GRAB INTENT: We need to pull this to pass it to the Learner at the end
        intent = task_data.get("intent")

        logger.info(f"🚀 Starting Task [{task_id}] with {len(steps)} steps")

        for step_index, step in enumerate(steps):
            action = step.get("action")

            bus.publish(
                "execution_step_started",
                {"task_id": task_id, "step_index": step_index, "action": action},
                source="executor",
            )

            success, result = await self._execute_step_safe(
                task_id, step_index, step, context
            )

            if not success:
                bus.publish(
                    "task_failed",
                    {
                        "task_id": task_id,
                        "failed_step": step,
                        "error": result,
                    },
                    source="executor",
                )

                retry_manager.clear_task(task_id)
                logger.error(
                    f"❌ Task [{task_id}] failed at step {step_index}: {result}"
                )
                return

            # Update context dynamically
            context["last_result"] = result
            context["last_action"] = action

            bus.publish(
                "execution_step_success",
                {
                    "task_id": task_id,
                    "step_index": step_index,
                    "result": result,
                },
                source="executor",
            )
            logger.info(f"✅ Step {step_index} completed: {action}")

        # 🔄 CRITICAL UPGRADE FOR LEARNER:
        # We now pass the 'intent' and 'steps' back up so the learner can memorize them!
        bus.publish(
            "task_completed",
            {"task_id": task_id, "intent": intent, "steps": steps},
            source="executor",
        )

        retry_manager.clear_task(task_id)
        logger.info(f"🏁 Task [{task_id}] completed successfully")

    # -------------------------
    # Execute step with validation + resilience
    # -------------------------
    async def _execute_step_safe(self, task_id, step_index, step, context):
        action = step.get("action")
        args = step.get("args", {})

        if action in self.restricted_actions:
            return False, f"Restricted action blocked: {action}"

        # -------------------------
        # Window focus (if needed)
        # -------------------------
        window_title = context.get("window_title")
        if window_title:
            focused = await focus_manager.ensure_focus(window_title)
            if not focused:
                return False, f"Failed to focus target window: {window_title}"

        # -------------------------
        # Tool selection & execution
        # -------------------------
        tried_tools = []
        fallback_attempts = 0
        max_fallbacks = settings.MAX_RETRY_ATTEMPTS

        while fallback_attempts < max_fallbacks:
            tool_type, tool_instance = await tool_selector.select_best_tool(
                {"intent": action}, context, exclude=tried_tools
            )

            if not tool_instance:
                logger.error(f"No tool/plugin available for action: {action}")
                return False, f"No tool/plugin available for action: {action}"

            tried_tools.append(getattr(tool_instance, "name", tool_type))

            bus.publish(
                "tool_selected",
                {
                    "task_id": task_id,
                    "step_index": step_index,
                    "tool_type": tool_type,
                    "tool_name": getattr(tool_instance, "name", tool_type),
                },
                source="executor",
            )

            try:
                # 1. Execute capability validation
                success, result = await capability_registry.execute(
                    action, context, args
                )

                bus.publish(
                    "execution_strategy_used",
                    {
                        "task_id": task_id,
                        "step_index": step_index,
                        "tool_type": tool_type,
                        "tool_name": getattr(tool_instance, "name", tool_type),
                    },
                    source="executor",
                )

                if not success:
                    error_type = self._classify_error(result)
                else:
                    action_data = result if isinstance(result, dict) else {}
                    cap_intent = action_data.get("intent") or action
                    cap_args = action_data.get("args") or args

                    # 2. Resolve to the real tool mapping
                    resolved = resolve_tool_call(cap_intent, cap_args)
                    if not resolved:
                        return (
                            False,
                            f"No tool mapping for capability: {cap_intent}",
                        )

                    tool_name, tool_action, tool_args = resolved
                    tool = tool_registry.get_tool(tool_name)
                    if not tool:
                        return False, f"Tool not registered: {tool_name}"

                    # 3. Run the tool!
                    ok, tool_result = await tool.run(tool_action, tool_args)
                    if ok:
                        logger.debug(
                            f"Action '{action}' -> {tool_name}.{tool_action} OK"
                        )
                        return True, tool_result

                    result = tool_result
                    error_type = self._classify_error(result)

            except asyncio.TimeoutError:
                error_type = "timeout"
                result = "Execution timed out"
            except Exception as e:
                error_type = "exception"
                result = str(e)

                # Feed the hard exception to the error handler
                error_handler.handle_error(
                    e,
                    component="executor",
                    context={"task_id": task_id, "step": step_index},
                )

            # -------------------------
            # Retry logic
            # -------------------------
            if await retry_manager.should_retry(
                task_id, step_index, error_type=error_type
            ):
                logger.info(f"Retrying step {step_index} due to {error_type}")
                continue

            # -------------------------
            # Fallback logic
            # -------------------------
            next_tool_type = fallback_manager.get_fallback(tool_type)
            if next_tool_type:
                logger.info(f"Fallback: {tool_type} → {next_tool_type}")
                bus.publish(
                    "fallback_triggered",
                    {
                        "from": tool_type,
                        "to": next_tool_type,
                        "task_id": task_id,
                        "step_index": step_index,
                    },
                    source="executor",
                )
                fallback_attempts += 1
                continue

            return False, {
                "type": error_type,
                "message": result,
                "tried_tools": tried_tools,
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