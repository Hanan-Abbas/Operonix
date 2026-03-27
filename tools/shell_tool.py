import asyncio
import platform
import shlex
from core.event_bus import bus

class ShellTool:
    def __init__(self):
        self.name = "shell_tool"
        self.os_name = platform.system()

    async def run(self, action, args):
        """
        Main entry point for the Executor.
        Actions: execute
        """
        command = args.get("command")
        if not command:
            return False, "No command provided for shell operation."

        # Emit activity for the Dashboard
        await bus.emit("shell_op_started", {"command": command}, source="shell_tool")

        try:
            if action == "execute":
                return await self._execute(command)
            
            return False, f"Unknown shell action: {action}"

        except Exception as e:
            return False, f"Shell Error: {str(e)}"

    