import asyncio
import platform
import os
from core.event_bus import bus

class Executor:
    def __init__(self):
        self.os_name = platform.system()
        self.is_running = False
        # Mapping tool names to their class instances (to be loaded later)
        self.tools = {} 

    async def start(self):
        """Subscribe to plans coming from the Planner."""
        bus.subscribe("plan_ready", self.execute_plan)
        self.is_running = True
        print(f"⚙️ Executor: Online. Operating System: {self.os_name}")

    async def execute_plan(self, event):
        """
        Iterates through the steps of a plan and executes them.
        """
        task_id = event.data.get("task_id")
        steps = event.data.get("steps", [])
        
        print(f"⚙️ Executor: Starting execution for Task [{task_id}]...")

        for i, step in enumerate(steps):
            step_name = f"Step {i+1}: {step['action']} via {step['tool']}"
            
            # Update Dashboard on current progress
            await bus.emit("execution_step_started", {
                "task_id": task_id,
                "step_index": i,
                "description": step_name
            }, source="executor")

            success, result = await self._run_step(step)

            if success:
                print(f"✅ {step_name} completed.")
                await bus.emit("execution_step_success", {
                    "task_id": task_id,
                    "step_index": i,
                    "result": result
                }, source="executor")
            else:
                print(f"❌ {step_name} failed: {result}")
                await bus.emit("task_failed", {
                    "task_id": task_id,
                    "error": result,
                    "failed_step": step
                }, source="executor")
                return # Stop execution on failure

        # If all steps pass
        await bus.emit("task_completed", {"task_id": task_id}, source="executor")

    