"""
tools/shell_tool.py
────────────────────
Shell / command execution tool — Z-Order Aware Hybrid Execution Model.

Execution profiles
──────────────────
Profile A  Ghost  — asyncio.create_subprocess_shell, venv pre-activated,
                    output captured and returned for panel + dashboard display.

Profile B  Bridge — writes the command directly to the pts device of the
                    user's last-active terminal.  The command appears and
                    runs in their real shell as if they typed it.
                    Output is NOT captured (it belongs to the user's terminal).
                    We publish a bus event for the dashboard noting the injection.

Profile C  Lab    — spawns a new visible terminal window (gnome-terminal /
                    xterm / etc.) that runs the command and stays open for
                    interactive use.  Output is in the visible window.

Dual output routing
───────────────────
After every Ghost execution, publishes "command_output_ready" on the EventBus.
WebSocket bridge automatically forwards this to the dashboard.
Panel subscribes to action_completed (already wired) for the inline snippet.

For Bridge and Lab, publishes "command_dispatched" so the dashboard can log
that something was sent, even though we cannot capture the output.

Changes from original
──────────────────────
* _execute() is now the Ghost executor only.
* run() routes to _execute_ghost(), _execute_bridge(), _execute_lab()
  based on the "profile" key in args (set by executor.py after calling
  terminal_resolver.resolve()).
* All other intent handlers (create_dir, delete_dir, etc.) remain unchanged
  and default to Ghost behaviour.
"""
from __future__ import annotations

import asyncio
import logging
import os
import platform
import shlex
import shutil
import subprocess
from pathlib import Path
from typing import Optional

from core.event_bus import bus
from core.terminal_resolver import (
    AmbiguousTarget,
    BridgeTarget,
    GhostTarget,
    LabTarget,
    ResolveResult,
    terminal_resolver,
)

log = logging.getLogger("ShellTool")


class ShellTool:
    name = "shell_tool"
    tool_type = "shell_tool"

    supported_intents: set[str] = {
        "run_command", "install_package", "check_status",
        "git_op", "execute_script", "open_url", "search_web", "navigate",
        "create_dir", "delete_dir",
        "execute",
    }

    def can_handle(self, intent: str) -> bool:
        return intent in self.supported_intents

    def __init__(self) -> None:
        self.os_name = platform.system()
        self._is_windows = self.os_name == "Windows"
        bus.subscribe("shell_tool_intent_registered", self._on_intent_registered)

    # ── EventBus hook ─────────────────────────────────────────────────────── #

    def _on_intent_registered(self, event: object) -> None:
        intent = getattr(event, "data", {}).get("intent")
        if intent and isinstance(intent, str):
            self.supported_intents.add(intent)

    # ── Public entry point ─────────────────────────────────────────────────── #

    async def run(self, action: str, args: dict) -> tuple[bool, str]:
        """
        Dispatch to the correct handler.

        The executor injects args["_profile"] (a ResolveResult) when the
        command is a run_command / execute intent.  Other intents (create_dir,
        install_package, etc.) always use Ghost.
        """
        await bus.emit(
            "shell_op_started", {"action": action, "args": args}, source="shell_tool"
        )

        try:
            if action in ("execute", "run_command", "git_op",
                          "check_status", "execute_script", "navigate"):
                return await self._dispatch_with_profile(action, args)

            if action == "create_dir":
                return await self._create_dir(args)

            if action == "delete_dir":
                return await self._delete_dir(args)

            if action == "install_package":
                return await self._install_package(args)

            if action == "open_url":
                return await self._open_url(args)

            if args.get("command"):
                return await self._dispatch_with_profile(action, args)

            return False, f"ShellTool: unknown action '{action}'"

        except Exception as exc:
            return False, f"ShellTool error [{action}]: {exc}"

    # ── Profile dispatcher ─────────────────────────────────────────────────── #

    async def _dispatch_with_profile(
        self, action: str, args: dict
    ) -> tuple[bool, str]:
        """
        Read the pre-resolved profile from args["_profile"] (injected by the
        executor).  If not present, resolve now using terminal_resolver.

        panel_sudo is handled here before the normal profile objects — it is
        signalled by args["needs_password"]=True or profile_hint="panel_sudo"
        and does NOT go through terminal_resolver (it uses its own subprocess
        flow with `sudo -S` and panel password widget).
        """
        command = args.get("command") or args.get("cmd", "")
        if not command:
            return False, "ShellTool: no command provided."

        # ── panel_sudo: password entered in the panel, piped to sudo -S ────────
        if args.get("needs_password") or args.get("profile_hint") == "panel_sudo":
            return await self._execute_panel_sudo(command, args)

        profile: Optional[ResolveResult] = args.get("_profile")

        if profile is None:
            # Executor did not pre-resolve — do it now (fallback)
            cwd = args.get("cwd")
            profile_hint = args.get("profile_hint")  # "ghost"|"bridge"|"lab"|None
            venv_path = args.get("venv_path")
            profile = terminal_resolver.resolve(
                cwd=cwd,
                command=command,
                profile_hint=profile_hint,
                venv_path=venv_path,
            )

        if isinstance(profile, GhostTarget):
            return await self._execute_ghost(command, args, profile)

        if isinstance(profile, BridgeTarget):
            return await self._execute_bridge(command, args, profile)

        if isinstance(profile, LabTarget):
            return await self._execute_lab(command, args, profile)

        if isinstance(profile, AmbiguousTarget):
            log.warning(
                "ShellTool: AmbiguousTarget reached _dispatch — falling back to Ghost"
            )
            ghost = GhostTarget(cwd=args.get("cwd"))
            return await self._execute_ghost(command, args, ghost)

        return False, f"ShellTool: unknown profile type {type(profile)}"

    # ── Profile: panel_sudo ───────────────────────────────────────────────── #

    async def _execute_panel_sudo(
        self, command: str, args: dict
    ) -> tuple[bool, str]:
        """
        Panel sudo profile — shows a password input widget in the panel,
        then runs the command via `sudo -S` with the password piped to stdin.

        Flow:
          1. Publish "sudo_password_required" on the bus.
             panel_controller subscribes → shows password widget in panel.
          2. Await "sudo_password_provided" (max 120 s).
             panel_controller emits this after the user submits the password.
          3. Build the command:
               - If command already starts with "sudo": run as-is with -S flag.
               - Otherwise: prepend "sudo -S".
          4. Run via asyncio subprocess with password written to stdin.
          5. Stream output back via "command_output_ready" and return result.

        `sudo -S` reads the password from stdin.  We write "<password>\n" to
        the process stdin immediately after it starts, then close stdin so sudo
        stops waiting.  Output (stdout + stderr) is captured and shown in the
        panel inline output area and the dashboard log.
        """
        task_id = args.get("task_id", "unknown")
        cwd     = args.get("cwd") or str(os.path.expanduser("~"))

        # ── Step 1: ask the panel to show a password widget ────────────────────
        log.info("panel_sudo: requesting password from panel for task=%s", task_id)
        bus.publish(
            "sudo_password_required",
            {
                "task_id": task_id,
                "command": command,
                "message": f"Enter sudo password to run: {command}",
            },
            source="shell_tool",
        )

        # ── Step 2: wait for the panel to provide the password ─────────────────
        loop    = asyncio.get_running_loop()
        fut: asyncio.Future = loop.create_future()

        def _on_password_provided(event: object) -> None:
            data = getattr(event, "data", {}) or {}
            if data.get("task_id") == task_id and not fut.done():
                fut.set_result(data.get("password", ""))

        bus.subscribe("sudo_password_provided", _on_password_provided)

        try:
            password = await asyncio.wait_for(fut, timeout=120.0)
        except asyncio.TimeoutError:
            log.warning("panel_sudo: password not provided within 120 s — cancelled")
            bus.publish(
                "sudo_password_cancelled",
                {"task_id": task_id, "reason": "timeout"},
                source="shell_tool",
            )
            return False, "sudo: password prompt timed out — command cancelled."
        finally:
            # Always unsubscribe to avoid memory leaks
            try:
                from core.event_bus import bus as _bus
                _bus._unsubscribe_dead(_on_password_provided)
            except Exception:
                pass

        if not password:
            return False, "sudo: no password provided — command cancelled."

        # ── Step 3: build sudo -S command ──────────────────────────────────────
        stripped = command.strip()
        if stripped.startswith("sudo "):
            # Already has sudo — insert -S after "sudo"
            sudo_command = "sudo -S " + stripped[len("sudo "):]
        else:
            sudo_command = f"sudo -S {stripped}"

        log.info("panel_sudo: executing '%s' (cwd=%s)", sudo_command, cwd)

        # ── Step 4: run subprocess with password piped to stdin ────────────────
        try:
            process = await asyncio.create_subprocess_shell(
                sudo_command,
                stdin  = asyncio.subprocess.PIPE,
                stdout = asyncio.subprocess.PIPE,
                stderr = asyncio.subprocess.PIPE,
                cwd    = cwd,
            )

            # Write password + newline, then close stdin so sudo stops waiting
            stdout_bytes, stderr_bytes = await process.communicate(
                input=(password + "\n").encode("utf-8")
            )

            exit_code = process.returncode
            stdout    = stdout_bytes.decode(errors="replace").strip()
            stderr    = stderr_bytes.decode(errors="replace").strip()

            # sudo echoes "Sorry, try again." to stderr on wrong password
            if "try again" in stderr.lower() or "incorrect password" in stderr.lower():
                log.warning("panel_sudo: incorrect password for task=%s", task_id)
                bus.publish(
                    "command_output_ready",
                    {
                        "profile":   "panel_sudo",
                        "command":   command,
                        "stdout":    "",
                        "stderr":    "Incorrect sudo password.",
                        "exit_code": exit_code,
                        "cwd":       cwd,
                        "success":   False,
                        "snippet":   "❌ Incorrect sudo password.",
                        "task_id":   task_id,
                    },
                    source="shell_tool",
                )
                return False, "sudo: incorrect password."

            ok     = exit_code == 0
            output = stdout or stderr or ("Done." if ok else f"Exit {exit_code}")
            snippet = (output[:300] + "…") if len(output) > 300 else output

        except Exception as exc:
            log.error("panel_sudo: subprocess failed — %s", exc)
            bus.publish(
                "command_output_ready",
                {
                    "profile":   "panel_sudo",
                    "command":   command,
                    "stdout":    "",
                    "stderr":    str(exc),
                    "exit_code": 1,
                    "cwd":       cwd,
                    "success":   False,
                    "snippet":   f"Error: {exc}",
                    "task_id":   task_id,
                },
                source="shell_tool",
            )
            return False, f"panel_sudo subprocess error: {exc}"

        # ── Step 5: publish dual output ────────────────────────────────────────
        bus.publish(
            "command_output_ready",
            {
                "profile":   "panel_sudo",
                "command":   command,
                "stdout":    stdout,
                "stderr":    stderr,
                "exit_code": exit_code,
                "cwd":       cwd,
                "success":   ok,
                "snippet":   snippet,
                "task_id":   task_id,
            },
            source="shell_tool",
        )

        log.info(
            "panel_sudo: task=%s exit_code=%d success=%s",
            task_id, exit_code, ok,
        )
        return ok, output

    # ── Profile A: Ghost ──────────────────────────────────────────────────── #

    async def _execute_ghost(
        self, command: str, args: dict, profile: GhostTarget
    ) -> tuple[bool, str]:
        """
        Silent subprocess.  If a venv activate script is known, prepend it.
        Publishes command_output_ready on the bus for panel + dashboard.
        """
        effective_command = command
        if profile.venv_path:
            activate = str(profile.venv_path).rstrip("/")
            if not activate.endswith("activate"):
                activate = os.path.join(activate, "bin", "activate")
            if os.path.exists(activate):
                effective_command = f". {shlex.quote(activate)} && {command}"
                log.debug("Ghost: venv pre-activated via %s", activate)

        cwd = profile.cwd or args.get("cwd") or None

        ok, output = await self._raw_subprocess(effective_command, cwd=cwd)

        # Publish for dual output routing
        bus.publish(
            "command_output_ready",
            {
                "profile":   "ghost",
                "command":   command,
                "stdout":    output if ok else "",
                "stderr":    "" if ok else output,
                "exit_code": 0 if ok else 1,
                "cwd":       cwd,
                "success":   ok,
                "snippet":   (output[:300] + "…") if len(output) > 300 else output,
            },
            source="shell_tool",
        )
        return ok, output

    # ── Profile B: Bridge ─────────────────────────────────────────────────── #

    async def _execute_bridge(
        self, command: str, args: dict, profile: BridgeTarget
    ) -> tuple[bool, str]:
        """
        Write the command string directly to the pts device of the user's
        active terminal.  The command appears in their shell as if typed.

        We cannot capture output — it is displayed in their terminal window.
        Returns success=True if the write succeeded.
        """
        pts_path = profile.pts_path
        if not pts_path or not os.path.exists(pts_path):
            log.warning(
                "Bridge: pts %s not available — falling back to Ghost", pts_path
            )
            ghost = GhostTarget(cwd=profile.cwd or args.get("cwd"))
            return await self._execute_ghost(command, args, ghost)

        try:
            payload = (command + "\n").encode("utf-8")
            # O_NOCTTY so we don't accidentally become the controlling process
            fd = os.open(pts_path, os.O_WRONLY | os.O_NOCTTY)
            try:
                os.write(fd, payload)
            finally:
                os.close(fd)

            msg = (
                f"Command injected into terminal '{profile.window_title}' "
                f"({pts_path}). Output is in that window."
            )
            log.info("Bridge: injected %r into %s", command, pts_path)

            # Notify dashboard that something was dispatched
            bus.publish(
                "command_dispatched",
                {
                    "profile":       "bridge",
                    "command":       command,
                    "pts":           pts_path,
                    "window_title":  profile.window_title,
                    "note":          "Output is in the user's terminal window.",
                    "success":       True,
                },
                source="shell_tool",
            )
            return True, msg

        except PermissionError:
            log.warning(
                "Bridge: no write permission to %s — falling back to Ghost", pts_path
            )
            ghost = GhostTarget(cwd=profile.cwd or args.get("cwd"))
            return await self._execute_ghost(command, args, ghost)

        except Exception as exc:
            log.error("Bridge: pts write failed — %s", exc)
            return False, f"Bridge injection failed: {exc}"

    # ── Profile C: Lab ────────────────────────────────────────────────────── #

    async def _execute_lab(
        self, command: str, args: dict, profile: LabTarget
    ) -> tuple[bool, str]:
        """
        Spawn an independent visible terminal running the command.
        The terminal stays open after the command completes (exec bash).
        """
        cwd = profile.cwd or args.get("cwd") or str(Path.home())
        bin_ = profile.terminal_bin or "xterm"

        # Build the terminal invocation — varies by emulator
        if "gnome-terminal" in bin_:
            term_cmd = [
                bin_, f"--working-directory={cwd}",
                "--", "bash", "-c",
                f"{command}; echo ''; echo '[Operonix] Done. Shell kept open.'; exec bash",
            ]
        elif "xterm" in bin_:
            term_cmd = [
                bin_, "-e",
                f"bash -c {shlex.quote(command + '; exec bash')}",
            ]
        elif bin_ in ("kitty", "alacritty"):
            term_cmd = [
                bin_, "--", "bash", "-c",
                f"{command}; exec bash",
            ]
        else:
            # Generic fallback
            term_cmd = [
                bin_, "-e",
                f"bash -c {shlex.quote(command + '; exec bash')}",
            ]

        try:
            # Detached — we do not wait for it to finish
            subprocess.Popen(
                term_cmd,
                cwd=cwd,
                start_new_session=True,
                stdout=subprocess.DEVNULL,
                stderr=subprocess.DEVNULL,
            )
            msg = f"Launched in new terminal window ({bin_}): {command}"
            log.info("Lab: %s", msg)

            bus.publish(
                "command_dispatched",
                {
                    "profile":  "lab",
                    "command":  command,
                    "terminal": bin_,
                    "cwd":      cwd,
                    "note":     "Running in a new visible terminal window.",
                    "success":  True,
                },
                source="shell_tool",
            )
            return True, msg

        except FileNotFoundError:
            log.error("Lab: terminal binary not found — %s", bin_)
            # Fall back to Ghost as last resort
            ghost = GhostTarget(cwd=cwd)
            return await self._execute_ghost(command, args, ghost)

        except Exception as exc:
            log.error("Lab: spawn failed — %s", exc)
            return False, f"Lab terminal spawn failed: {exc}"

    # ── Low-level subprocess runner (Ghost only) ──────────────────────────── #

    async def _raw_subprocess(
        self, command: str, cwd: Optional[str] = None
    ) -> tuple[bool, str]:
        try:
            process = await asyncio.create_subprocess_shell(
                command,
                stdout=asyncio.subprocess.PIPE,
                stderr=asyncio.subprocess.PIPE,
                cwd=cwd or None,
                executable="cmd.exe" if self._is_windows else None,
            )
            stdout, stderr = await process.communicate()
            exit_code = process.returncode
            output = stdout.decode(errors="replace").strip()
            error  = stderr.decode(errors="replace").strip()

            if exit_code == 0:
                return True, output or "Command executed successfully."
            return False, f"Exit {exit_code}: {error or output}"

        except Exception as exc:
            return False, f"Subprocess failed: {exc}"

    # ── Intent handlers (always Ghost) ────────────────────────────────────── #

    async def _create_dir(self, args: dict) -> tuple[bool, str]:
        path = self._resolve_path_from_args(args)
        if not path:
            return False, "ShellTool create_dir: no path or dir_name provided."
        command = (
            f'mkdir "{path}"' if self._is_windows
            else f"mkdir -p {shlex.quote(path)}"
        )
        ok, result = await self._raw_subprocess(command)
        if ok:
            return True, f"Directory created: {path}"
        if "already exists" in result.lower() or "file exists" in result.lower():
            return True, f"Directory already exists (OK): {path}"
        return False, result

    async def _delete_dir(self, args: dict) -> tuple[bool, str]:
        path = self._resolve_path_from_args(args)
        if not path:
            return False, "ShellTool delete_dir: no path or dir_name provided."
        recursive = bool(args.get("recursive", False))
        command = (
            (f'rmdir /S /Q "{path}"' if recursive else f'rmdir "{path}"')
            if self._is_windows
            else (f"rm -rf {shlex.quote(path)}" if recursive else f"rmdir {shlex.quote(path)}")
        )
        ok, result = await self._raw_subprocess(command)
        if ok:
            return True, f"Directory deleted: {path}"
        return False, result

    async def _install_package(self, args: dict) -> tuple[bool, str]:
        package = args.get("package") or args.get("name", "")
        manager = args.get("manager", "pip").lower()
        if not package:
            return False, "ShellTool install_package: no package name provided."
        command = (
            f"npm install {shlex.quote(package)}"
            if manager == "npm"
            else f"pip install {shlex.quote(package)}"
        )
        return await self._raw_subprocess(command)

    async def _open_url(self, args: dict) -> tuple[bool, str]:
        url = args.get("url") or args.get("link", "")
        if not url:
            return False, "ShellTool open_url: no URL provided."
        if self._is_windows:
            command = f'start "" "{url}"'
        elif self.os_name == "Darwin":
            command = f"open {shlex.quote(url)}"
        else:
            command = f"xdg-open {shlex.quote(url)}"
        return await self._raw_subprocess(command)

    # ── Utility ───────────────────────────────────────────────────────────── #

    @staticmethod
    def _resolve_path_from_args(args: dict) -> str:
        if args.get("path"):
            return str(args["path"]).strip()
        if args.get("dir_name"):
            return str(args["dir_name"]).strip()
        return ""


shell_tool = ShellTool()