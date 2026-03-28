import asyncio
import platform
import os

from core.event_bus import bus
from tools.tool_registry import tool_registry
from tools.tool_selector import tool_selector

from core.retry_manager import retry_manager
from core.fallback_manager import fallback_manager
from core.focus_manager import focus_manager


class Executor:
    def __init__(self):
        self.os_name = platform.system()
        self.is_running = False

        # Safety rules (expand later)
        self.restricted_actions = {"delete_file", "run_shell"}

    async def start(self):
        bus.subscribe("plan_ready", self.execute_plan)
        self.is_running = True

        print(f"⚙️ Executor: Online | OS: {self.os_name}")
        print(f"⚙️ Tools Loaded: {len(tool_registry.list_tools())}")

    # ===============================
    # MAIN EXECUTION LOOP
    # ===============================
    async def execute_plan(self, event):
        task_id = event.data.get("task_id")
        steps = event.data.get("steps", [])
        context = event.data.get("context", {})

        print(f"\n🚀 Starting Task [{task_id}] with {len(steps)} steps")

        for step_index, step in enumerate(steps):
            action = step.get("action")

            await bus.emit("execution_step_started", {
                "task_id": task_id,
                "step_index": step_index,
                "action": action
            })

            success, result = await self._execute_step_with_resilience(
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

            # Update context dynamically
            context["last_result"] = result
            context["last_action"] = action

            await bus.emit("execution_step_success", {
                "task_id": task_id,
                "step_index": step_index,
                "result": result
            })

        await bus.emit("task_completed", {"task_id": task_id})
        retry_manager.clear_task(task_id)

    # ===============================
    # RESILIENT STEP EXECUTION
    # ===============================
    async def _execute_step_with_resilience(self, task_id, step_index, step, context):
        action = step.get("action")
        args = step.get("args", {})

        if "path" in args:
            args["path"] = self._normalize_path(args["path"])

        # Safety Check
        if action in self.restricted_actions:
            return False, f"Restricted action blocked: {action}"

        # Focus Handling
        if context.get("window_title"):
            focused = await focus_manager.ensure_focus(context["window_title"])
            if not focused:
                return False, "Failed to focus target window"

        tried_tools = []
        error_type = None

        while True:
            # Select best tool
            tool_type, tool_instance = await tool_selector.select_best_tool(
                {"intent": action},
                context,
                exclude=tried_tools
            )

            if not tool_instance:
                return False, f"No tool available for action: {action}"

            tried_tools.append(tool_instance.name)

            await bus.emit("tool_selected", {
                "task_id": task_id,
                "step_index": step_index,
                "tool_type": tool_type,
                "tool_name": tool_instance.name
            })

            try:
                success, result = await asyncio.wait_for(
                    tool_instance.run(action, args),
                    timeout=10
                )

                await bus.emit("execution_strategy_used", {
                    "type": tool_type,
                    "tool": tool_instance.name
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

            # ===============================
            # RETRY LOGIC
            # ===============================
            should_retry = await retry_manager.should_retry(
                task_id,
                step_index,
                error_type=error_type
            )

            if should_retry:
                continue

            # ===============================
            # FALLBACK LOGIC
            # ===============================
            next_tool_type = fallback_manager.get_fallback(tool_type)

            if next_tool_type:
                await bus.emit("fallback_triggered", {
                    "from": tool_type,
                    "to": next_tool_type
                })
                continue

            return False, {
                "type": error_type,
                "message": result,
                "tried_tools": tried_tools
            }

    # ===============================
    # HELPERS
    # ===============================
    def _normalize_path(self, path):
        return os.path.normpath(path)

    def _classify_error(self, result):
        text = str(result).lower()

        if "permission" in text:
            return "permission_denied"
        if "not found" in text:
            return "not_found"
        if "timeout" in text:
            return "timeout"

        return "unknown_error"


# Global instance
executor = Executor()
