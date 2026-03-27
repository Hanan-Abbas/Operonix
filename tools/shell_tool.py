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

    async def _execute(self, command):
        """
        Runs a command in the system's native shell asynchronously.
        """
        try:
            # Cross-Platform Shell Selection
            # Windows: uses cmd.exe /c
            # Linux/Mac: uses the default system shell (usually sh or bash)
            is_windows = self.os_name == "Windows"
            
            process = await asyncio.create_subprocess_shell(
                command,
                stdout=asyncio.subprocess.PIPE,
                stderr=asyncio.subprocess.PIPE,
                # On Windows, we often need to explicitly point to the executable for complex commands
                executable="cmd.exe" if is_windows else None 
            )

            # Wait for the command to finish and capture output
            stdout, stderr = await process.communicate()
            
            exit_code = process.returncode
            output = stdout.decode().strip()
            error = stderr.decode().strip()

            if exit_code == 0:
                result = output if output else "Command executed successfully (no output)."
                return True, result
            else:
                return False, f"Exit Code {exit_code}: {error if error else output}"

        except Exception as e:
            return False, f"Execution failed: {str(e)}"

# Global instance
shell_tool = ShellTool()