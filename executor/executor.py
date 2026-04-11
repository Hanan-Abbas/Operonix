import asyncio
import logging
import platform
import json

from capabilities.registry import capability_registry
from context.context_validator import context_validator
from core.config import settings
from core.error_handler import ErrorHandler
from core.event_bus import bus
from executor.fallback_manager import FallbackManager
from executor.focus_manager import FocusManager
from executor.retry_manager import RetryManager
from tools.tool_registry import tool_registry
from tools.tool_selector import tool_selector
from executor.error_classifier import error_classifier
from core.metrics import metrics
from brain.llm_client import llm_client 

logger = logging.getLogger("Executor")

error_handler = ErrorHandler(event_bus=bus, logger=logger)

retry_manager = RetryManager()
fallback_manager = FallbackManager()
focus_manager = FocusManager()


class Executor:

    def __init__(self):
        self.os_name = platform.system()
        self.is_running = False
        self.restricted_actions = set()

    async def start(self):
        bus.subscribe("task_safety_cleared", self.execute_plan)
        self.is_running = True
        logger.info(f"⚙️ Executor Online | OS: {self.os_name}")
        logger.info(f"⚙️ Tools Loaded: {len(tool_registry.list_tools())}")

    async def execute_plan(self, event):

        metrics.total_tasks += 1
        start = time.time()

        task_data = event.data
        task_id = task_data.get("task_id")
        steps = task_data.get("steps", [])
        context = task_data.get("context", {})
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

        metrics.total_duration_seconds += time.time() - start
        metrics.successful_tasks += 1
        
        logger.info(
            f"📊 Success rate: {metrics.success_rate():.1f}% "
            f"| Avg duration: {metrics.avg_task_duration():.2f}s"
        )

        bus.publish(
            "task_completed",
            {"task_id": task_id, "intent": intent, "steps": steps},
            source="executor",
        )

        retry_manager.clear_task(task_id)
        logger.info(f"🏁 Task [{task_id}] completed successfully")

    async def _execute_step_safe(self, task_id, step_index, step, context):
        action = step.get("action")
        args = step.get("args", {})

        if action in self.restricted_actions:
            return False, f"Restricted action blocked: {action}"

        window_title = context.get("window_title")
        if window_title:
            focused = await focus_manager.ensure_focus(window_title)
            if not focused:
                return False, f"Failed to focus target window: {window_title}"

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
                success, result = await capability_registry.execute(
                    action, context, args
                )
                # ✅ Use classifier
                category = await error_classifier.classify(error_str)
                strategy = await error_classifier.get_retry_strategy(category)

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
                    # 🟢 DYNAMIC: Ask Ollama to classify what went wrong
                    error_type = await self._classify_error_dynamically(result)
                else:
                    action_data = result if isinstance(result, dict) else {}
                    cap_intent = action_data.get("intent") or action
                    cap_args = action_data.get("args") or args

                    # 🟢 NEW: Clean fallback resolve method
                    resolved = self._resolve_tool_call(cap_intent, cap_args)
                    if not resolved:
                        return (False, f"No tool mapping for capability: {cap_intent}")

                    tool_name, tool_action, tool_args = resolved
                    tool = tool_registry.get_tool(tool_name)
                    if not tool:
                        return False, f"Tool not registered: {tool_name}"

                    ok, tool_result = await tool.run(tool_action, tool_args)
                    if ok:
                        logger.debug(f"Action '{action}' -> {tool_name}.{tool_action} OK")
                        return True, tool_result

                    result = tool_result
                    error_type = await self._classify_error_dynamically(result)

            except asyncio.TimeoutError:
                error_type = "timeout"
                result = "Execution timed out"
            except Exception as e:
                error_type = "exception"
                result = str(e)

                error_handler.handle_error(
                    e,
                    component="executor",
                    context={"task_id": task_id, "step": step_index},
                )

            category = await error_classifier.classify(error_str)
            strategy = await error_classifier.get_retry_strategy(category)
            
            self.logger.warning(
                f"Step {step_index} failed ({category}): {strategy['suggestion']}"
            )
            
            # ✅ Decide whether to retry
            if strategy["should_retry"]:
                backoff_ms = strategy.get("backoff_ms", 1000)
                await asyncio.sleep(backoff_ms / 1000.0)
                # Try again...
            else:
                # Give up
                return False, error_str

            if await retry_manager.should_retry(
                task_id, step_index, error_type=error_type
            ):
                logger.info(f"Retrying step {step_index} due to {error_type}")
                continue

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

    async def _classify_error_dynamically(self, result) -> str:
        """Classify error with fallback patterns."""
        
        result_str = str(result).lower()
        
        # ✅ FAST PATH: Regex fallback (no LLM)
        fallback_patterns = {
            "permission_denied": r"(permission|denied|access|forbidden|not allowed)",
            "not_found": r"(not found|no such|does not exist|404)",
            "timeout": r"(timeout|timed out|deadline|took too long)",
            "network": r"(connection|network|unreachable|offline)",
        }
        
        for category, pattern in fallback_patterns.items():
            if re.search(pattern, result_str):
                self.logger.info(f"Error classified as '{category}' (regex)")
                return category
        
        # ✅ SLOW PATH: Try LLM if available
        try:
            response = await asyncio.wait_for(
                llm_client.generate(prompt, use_json=True),
                timeout=2.0  # Quick timeout
            )
            data = json.loads(response)
            category = data.get("category", "unknown_error")
            self.logger.info(f"Error classified as '{category}' (LLM)")
            return category
        except (asyncio.TimeoutError, json.JSONDecodeError, Exception) as e:
            self.logger.warning(f"LLM classification failed: {e}. Defaulting to 'unknown_error'")
            return "unknown_error"

    # 🟢 NEW: Resolves tool calls mapping directly to the tool registry
    def _resolve_tool_call(self, intent: str, args: dict):
        """Maps an abstract intent to registered concrete tools in tool_registry."""
        for tool_name, tool_obj in tool_registry.list_tools().items():
            if hasattr(tool_obj, "can_handle") and tool_obj.can_handle(intent):
                return tool_name, intent, args
        return None


executor = Executor()