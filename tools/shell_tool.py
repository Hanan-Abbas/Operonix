"""
tools/shell_tool.py
────────────────────
Shell / command execution tool.

Changes from original
──────────────────────
BUG FIX 1 — create_dir / delete_dir were missing from supported_intents.
    The executor's _resolve_tool_call() calls can_handle(intent) to find a
    tool.  Without these entries the tool was never selected for directory ops.

BUG FIX 2 — run() only handled action=="execute" and required a pre-built
    "command" string in args.  create_dir arrives with {dir_name, location,
    path} — no "command" key.  We now add intent-specific action handlers
    that build the correct OS-aware shell command from structured args, then
    delegate to _execute().

BUG FIX 3 — can_handle() now also returns True for any intent in
    supported_intents so _resolve_tool_call() in the executor works correctly.

Self-evolving hook
──────────────────
Tools can subscribe to "shell_tool_intent_registered" on the EventBus to
add new intents at runtime without restarting (e.g. from a generated plugin).
"""
from __future__ import annotations

import asyncio
import platform
import shlex
from pathlib import Path

from core.event_bus import bus


class ShellTool:
    name = "shell_tool"
    tool_type = "shell_tool"

    supported_intents: set[str] = {
        # Original intents
        "run_command", "install_package", "check_status",
        "git_op", "execute_script", "open_url", "search_web", "navigate",
        # NEW — directory operations
        "create_dir", "delete_dir",
        # NEW — generic execute alias
        "execute",
    }

    def can_handle(self, intent: str) -> bool:
        return intent in self.supported_intents

    def __init__(self) -> None:
        self.os_name = platform.system()
        self._is_windows = self.os_name == "Windows"
        # Allow runtime registration of new intents (self-evolving hook)
        bus.subscribe("shell_tool_intent_registered", self._on_intent_registered)

    # ── EventBus hook ─────────────────────────────────────────────────── #

    def _on_intent_registered(self, event: object) -> None:
        """Add a new supported intent at runtime without restarting."""
        intent = getattr(event, "data", {}).get("intent")
        if intent and isinstance(intent, str):
            self.supported_intents.add(intent)

    # ── Public entry point ─────────────────────────────────────────────── #

    async def run(self, action: str, args: dict) -> tuple[bool, str]:
        """
        Dispatch to the correct handler based on action/intent name.

        action values recognised:
          execute / run_command  — pre-built command string in args["command"]
          create_dir             — build mkdir from args["path"] or args["dir_name"]
          delete_dir             — build rmdir from args["path"] or args["dir_name"]
          install_package        — build pip/npm install from args
          git_op                 — pass-through to execute
          check_status           — pass-through to execute
          execute_script         — pass-through to execute
        """
        await bus.emit(
            "shell_op_started", {"action": action, "args": args}, source="shell_tool"
        )

        try:
            if action in ("execute", "run_command", "git_op",
                          "check_status", "execute_script", "navigate"):
                return await self._run_raw_command(args)

            if action == "create_dir":
                return await self._create_dir(args)

            if action == "delete_dir":
                return await self._delete_dir(args)

            if action == "install_package":
                return await self._install_package(args)

            if action == "open_url":
                return await self._open_url(args)

            # Unknown action — try treating it as a raw command as last resort
            if args.get("command"):
                return await self._run_raw_command(args)

            return False, f"ShellTool: unknown action '{action}'"

        except Exception as exc:
            return False, f"ShellTool error [{action}]: {exc}"

    # ── Action handlers ───────────────────────────────────────────────── #

    async def _run_raw_command(self, args: dict) -> tuple[bool, str]:
        """Execute a pre-built command string."""
        command = args.get("command") or args.get("cmd", "")
        if not command:
            return False, "ShellTool: no command provided."
        return await self._execute(str(command))

    async def _create_dir(self, args: dict) -> tuple[bool, str]:
        """
        Build and run the OS mkdir command from structured args.

        Accepts:
          path      — explicit full path (highest priority)
          dir_name  — directory name (resolved by file_ops._resolve_path
                      before reaching here, so "path" should already be set)
        """
        path = self._resolve_path_from_args(args)
        if not path:
            return False, "ShellTool create_dir: no path or dir_name provided."

        # Use the OS-appropriate command
        if self._is_windows:
            # mkdir on Windows creates intermediate dirs by default
            command = f'mkdir "{path}"'
        else:
            command = f"mkdir -p {shlex.quote(path)}"

        ok, result = await self._execute(command)
        if ok:
            return True, f"Directory created: {path}"
        # Treat "already exists" as success (idempotent)
        if "already exists" in result.lower() or "file exists" in result.lower():
            return True, f"Directory already exists (OK): {path}"
        return False, result

    async def _delete_dir(self, args: dict) -> tuple[bool, str]:
        """Build and run the OS rmdir/rm command."""
        path = self._resolve_path_from_args(args)
        if not path:
            return False, "ShellTool delete_dir: no path or dir_name provided."

        recursive = bool(args.get("recursive", False))

        if self._is_windows:
            command = f'rmdir /S /Q "{path}"' if recursive else f'rmdir "{path}"'
        else:
            command = (
                f"rm -rf {shlex.quote(path)}" if recursive
                else f"rmdir {shlex.quote(path)}"
            )

        ok, result = await self._execute(command)
        if ok:
            return True, f"Directory deleted: {path}"
        return False, result

    async def _install_package(self, args: dict) -> tuple[bool, str]:
        """pip install <package> or npm install <package>."""
        package = args.get("package") or args.get("name", "")
        manager = args.get("manager", "pip").lower()
        if not package:
            return False, "ShellTool install_package: no package name provided."

        if manager == "npm":
            command = f"npm install {shlex.quote(package)}"
        else:
            command = f"pip install {shlex.quote(package)}"

        return await self._execute(command)

    async def _open_url(self, args: dict) -> tuple[bool, str]:
        """Open a URL in the default browser."""
        url = args.get("url") or args.get("link", "")
        if not url:
            return False, "ShellTool open_url: no URL provided."

        if self._is_windows:
            command = f'start "" "{url}"'
        elif self.os_name == "Darwin":
            command = f"open {shlex.quote(url)}"
        else:
            command = f"xdg-open {shlex.quote(url)}"

        return await self._execute(command)

    # ── Low-level subprocess runner ───────────────────────────────────── #

    async def _execute(self, command: str) -> tuple[bool, str]:
        try:
            process = await asyncio.create_subprocess_shell(
                command,
                stdout=asyncio.subprocess.PIPE,
                stderr=asyncio.subprocess.PIPE,
                executable="cmd.exe" if self._is_windows else None,
            )
            stdout, stderr = await process.communicate()
            exit_code = process.returncode
            output = stdout.decode(errors="replace").strip()
            error = stderr.decode(errors="replace").strip()

            if exit_code == 0:
                return True, output or "Command executed successfully."
            return False, f"Exit {exit_code}: {error or output}"

        except Exception as exc:
            return False, f"Subprocess failed: {exc}"

    # ── Utility ───────────────────────────────────────────────────────── #

    @staticmethod
    def _resolve_path_from_args(args: dict) -> str:
        """
        Return the best path string from structured args.
        file_ops._resolve_path() should have already written a "path" key;
        this is a safety fallback.
        """
        if args.get("path"):
            return str(args["path"]).strip()
        if args.get("dir_name"):
            return str(args["dir_name"]).strip()
        return ""


shell_tool = ShellTool()