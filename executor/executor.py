import asyncio
import platform
import os
from core.event_bus import bus
from tools.tool_registry import tool_registry
from tools.tool_selector import tool_selector

class Executor:
    def __init__(self):
        self.os_name = platform.system()
        self.is_running = False

    async def start(self):
        """Initialize subscriptions for the execution layer."""
        bus.subscribe("plan_ready", self.execute_plan)
        self.is_running = True
        print(f"⚙️ Executor: Online. OS: {self.os_name}")
        print(f"⚙️ Registry Status: {len(tool_registry.list_tools())} tools loaded.")

    async def execute_plan(self, event):
        """
        Sequentially executes steps provided by the Planner.
        """
        task_id = event.data.get("task_id")
        steps = event.data.get("steps", [])
        context = event.data.get("context", {}) # Get active window/app context
        
        print(f"⚙️ Executor: Starting Task [{task_id}]...")

        for i, step in enumerate(steps):
            # 1. Notify the Event Bus (and Dashboard) that a step is starting
            await bus.emit("execution_step_started", {
                "task_id": task_id,
                "step_index": i,
                "action": step.get("action"),
                "description": f"Executing {step.get('action')}..."
            }, source="executor")

            # 2. Run the step logic
            success, result = await self._run_step(step, context)

            if success:
                print(f"✅ Step {i+1} Success")
                await bus.emit("execution_step_success", {
                    "task_id": task_id,
                    "step_index": i,
                    "result": result
                }, source="executor")
            else:
                # 3. Handle Failure: Stop and notify
                print(f"❌ Step {i+1} Failed: {result}")
                await bus.emit("task_failed", {
                    "task_id": task_id,
                    "error": result,
                    "failed_step": step
                }, source="executor")
                return # Stop the sequence on error

        # 4. Final Success Signal
        await bus.emit("task_completed", {"task_id": task_id}, source="executor")

    async def _run_step(self, step, context):
        """
        Uses the ToolSelector to choose the best path (Plugin -> Tool -> UI).
        """
        action = step.get("action")
        args = step.get("args", {})
        
        # Cross-OS Path Normalization for any path in args
        if "path" in args:
            args["path"] = self._normalize_path(args["path"])

        # ✅ LOGIC: Use ToolSelector to find the best implementation
        # This follows your hierarchy: Plugin -> Tool -> UI
        intent_data = {"intent": action}
        tool_type, target_instance = await tool_selector.select_best_tool(intent_data, context)

        if not target_instance:
            return False, f"No execution path found for action: {action}"

        try:
            # All tools and plugins must implement 'async run(action, args)'
            success, message = await target_instance.run(action, args)
            
            # Log the strategy used for debugging and dashboard
            await bus.emit("execution_strategy_used", {
                "type": tool_type,
                "tool": getattr(target_instance, 'name', 'plugin')
            }, source="executor")
            
            return success, message

        except Exception as e:
            return False, f"Execution Error in {tool_type}: {str(e)}"

    def _normalize_path(self, path):
        """Ensures file paths match the host OS syntax (e.g., / vs \\)."""
        return os.path.normpath(path)

# Global instance
executor = Executor()