import asyncio
import platform
import os
from core.event_bus import bus
from tools.tool_registry import tool_registry

class Executor:
    def __init__(self):
        self.os_name = platform.system()
        self.is_running = False

    async def start(self):
        """Initialize subscriptions for the execution layer."""
        bus.subscribe("plan_ready", self.execute_plan)
        self.is_running = True
        print(f"⚙️ Executor: Online. OS: {self.os_name}")
        print(f"⚙️ Registered Tools: {tool_registry.list_tools()}")

    async def execute_plan(self, event):
        """
        Sequentially executes steps provided by the Planner.
        """
        task_id = event.data.get("task_id")
        steps = event.data.get("steps", [])
        
        print(f"⚙️ Executor: Processing Task [{task_id}] ({len(steps)} steps)")

        for i, step in enumerate(steps):
            # Update Event Bus for Dashboard Tracking
            await bus.emit("execution_step_started", {
                "task_id": task_id,
                "step_index": i,
                "tool": step.get("tool"),
                "action": step.get("action")
            }, source="executor")

            success, result = await self._run_step(step)

            if success:
                await bus.emit("execution_step_success", {
                    "task_id": task_id,
                    "step_index": i,
                    "result": result
                }, source="executor")
            else:
                # 🚨 Stop execution and trigger Error Handler / Fallback
                await bus.emit("task_failed", {
                    "task_id": task_id,
                    "error": result,
                    "failed_at": step
                }, source="executor")
                return 

        await bus.emit("task_completed", {"task_id": task_id}, source="executor")

    async def _run_step(self, step):
        """
        Resolves the tool from the registry and executes the action.
        """
        tool_name = step.get("tool")
        action = step.get("action")
        args = step.get("args", {})

        # Cross-OS Path Normalization
        if "path" in args:
            args["path"] = self._normalize_path(args["path"])

        # Fetch tool from Tool Registry (Architecture-compliant)
        target_tool = tool_registry.get_tool(tool_name)

        if target_tool:
            try:
                # Every tool follows the 'async run' interface
                return await target_tool.run(action, args)
            except Exception as e:
                return False, f"Tool Execution Error [{tool_name}]: {str(e)}"
        
        return False, f"Tool Registry Error: '{tool_name}' not found."

    def _normalize_path(self, path):
        """Ensures file paths match the host OS syntax."""
        return os.path.normpath(path)

# Global instance
executor = Executor()