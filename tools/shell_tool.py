"""
tools/shell_tool.py
────────────────────
Shell / command execution tool.

Changes from original
──────────────────────
• Added `supported_intents` set — eliminates the need for the old
  _tool_matches_intent() hardcoded table in tool_selector.
• Added `can_handle(intent)` method.
• Everything else is identical to the original.
"""
from __future__ import annotations

import asyncio
import platform

from core.event_bus import bus


class ShellTool:
    name = "shell_tool"
    tool_type = "shell_tool"

    supported_intents: set[str] = {
        "run_command", "install_package", "check_status",
        "git_op", "execute_script", "open_url", "search_web", "navigate",
    }

    def can_handle(self, intent: str) -> bool:
        return intent in self.supported_intents

    def __init__(self) -> None:
        self.os_name = platform.system()

    async def run(self, action: str, args: dict):
        command = args.get("command")
        if not command:
            return False, "No command provided for shell operation."

        await bus.emit("shell_op_started", {"command": command}, source="shell_tool")

        try:
            if action == "execute":
                return await self._execute(command)
            return False, f"Unknown shell action: {action}"
        except Exception as e:
            return False, f"Shell Error: {str(e)}"

    async def _execute(self, command: str):
        try:
            is_windows = self.os_name == "Windows"
            process = await asyncio.create_subprocess_shell(
                command,
                stdout=asyncio.subprocess.PIPE,
                stderr=asyncio.subprocess.PIPE,
                executable="cmd.exe" if is_windows else None,
            )
            stdout, stderr = await process.communicate()
            exit_code = process.returncode
            output = stdout.decode().strip()
            error = stderr.decode().strip()

            if exit_code == 0:
                return True, output or "Command executed successfully (no output)."
            return False, f"Exit Code {exit_code}: {error or output}"
        except Exception as e:
            return False, f"Execution failed: {str(e)}"


shell_tool = ShellTool()