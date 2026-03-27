import asyncio
import platform
import os
from core.event_bus import bus

# Import your tools at the top level
from tools.file_tool import file_tool
# from tools.shell_tool import shell_tool  # Uncomment when you create this file
# from tools.ui_tool import ui_tool        # Uncomment when you create this file

class Executor:
    def __init__(self):
        self.os_name = platform.system()
        self.is_running = False
        
        # ✅ THE REGISTRY: This is the source of truth for all "Hands"
        # Whenever you add a new tool, simply add it to this dictionary.
        self.tools = {
            "file_tool": file_tool,
            # "shell_tool": shell_tool,
            # "ui_tool": ui_tool
        }

    async def start(self):
        """Subscribe to plans and prepare for execution."""
        bus.subscribe("plan_ready", self.execute_plan)
        self.is_running = True
        print(f"⚙️ Executor: Online. Operating System: {self.os_name}")

    async def execute_plan(self, event):
        """
        Iterates through the steps of a plan and executes them via the tool registry.
        """
        task_id = event.data.get("task_id")
        steps = event.data.get("steps", [])
        
        print(f"⚙️ Executor: Starting execution for Task [{task_id}]...")

        for i, step in enumerate(steps):
            step_name = f"Step {i+1}: {step['action']} via {step['tool']}"
            
            # 1. Notify Dashboard that a new step started
            await bus.emit("execution_step_started", {
                "task_id": task_id,
                "step_index": i,
                "description": step_name
            }, source="executor")

            # 2. Run the actual logic
            success, result = await self._run_step(step)

            if success:
                print(f"✅ {step_name} completed.")
                # 3. Notify Dashboard of success
                await bus.emit("execution_step_success", {
                    "task_id": task_id,
                    "step_index": i,
                    "result": result
                }, source="executor")
            else:
                print(f"❌ {step_name} failed: {result}")
                # 4. Notify Dashboard/Orchestrator of failure
                await bus.emit("task_failed", {
                    "task_id": task_id,
                    "error": result,
                    "failed_step": step
                }, source="executor")
                return # Stop execution sequence immediately

        # Final Success Event
        await bus.emit("task_completed", {"task_id": task_id}, source="executor")

    async def _run_step(self, step):
        """
        Dynamically finds the tool from the registry and runs the action.
        """
        tool_name = step.get("tool")
        action = step.get("action")
        args = step.get("args", {})

        # Path Normalization for Cross-OS Compatibility (Handled before tool call)
        if "path" in args:
            args["path"] = self._normalize_path(args["path"])

        try:
            # ✅ REGISTRY CHECK: Look for the tool in our dictionary
            if tool_name in self.tools:
                target_tool = self.tools[tool_name]
                # Every tool must have an 'async run(action, args)' method
                return await target_tool.run(action, args)
            
            # Special case for Shell if you haven't moved it to shell_tool.py yet
            elif tool_name == "shell_tool":
                return await self._execute_shell(args.get("command"))
            
            else:
                return False, f"Error: Tool '{tool_name}' is not registered in the Executor."

        except Exception as e:
            return False, f"Unexpected Executor Error: {str(e)}"

    def _normalize_path(self, path):
        """Ensures paths use the correct slash ( / vs \ ) for the current OS."""
        return os.path.normpath(path)

    async def _execute_shell(self, command):
        """Runs a command in the native OS shell (Internal fallback)."""
        shell_executable = "cmd.exe" if self.os_name == "Windows" else None
        
        try:
            process = await asyncio.create_subprocess_shell(
                command,
                stdout=asyncio.subprocess.PIPE,
                stderr=asyncio.subprocess.PIPE,
                executable=shell_executable
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