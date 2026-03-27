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

    async def _run_step(self, step):
        """
        Dispatches the step to the actual tool logic.
        """
        tool_name = step.get("tool")
        action = step.get("action")
        args = step.get("args", {})

        # Path Normalization for Cross-OS Compatibility
        if "path" in args:
            args["path"] = self._normalize_path(args["path"])

        try:
            # Note: In the next phase, we will populate self.tools with 
            # file_tool, shell_tool, etc. For now, we simulate the dispatch.
            
            if tool_name == "shell_tool":
                return await self._execute_shell(args.get("command"))
            
            # Placeholder for other tools (file_tool, ui_tool)
            # return await self.tools[tool_name].run(action, args)
            
            return True, f"Simulated {action} on {tool_name}"

        except Exception as e:
            return False, str(e)

    def _normalize_path(self, path):
        """Converts paths to the correct format for the current OS."""
        # Fixes slashes ( / vs \ ) automatically
        return os.path.normpath(path)

    async def _execute_shell(self, command):
        """Runs a command in the native OS shell."""
        # Adjust shell based on OS
        shell_executable = "cmd.exe" if self.os_name == "Windows" else "/bin/bash"
        
        try:
            process = await asyncio.create_subprocess_shell(
                command,
                stdout=asyncio.subprocess.PIPE,
                stderr=asyncio.subprocess.PIPE,
                executable=shell_executable if self.os_name == "Windows" else None
            )
            stdout, stderr = await process.communicate()
            
            if process.returncode == 0:
                return True, stdout.decode().strip()
            else:
                return False, stderr.decode().strip()
        except Exception as e:
            return False, str(e)

# Global instance
executor = Executor()