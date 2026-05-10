"""
tools/process_bridge.py
────────────────────────
Asynchronous Process Bridge — the "conscious observer" for interactive commands.

This module replaces the blind `process.communicate()` call with a real-time
streaming pipeline that reads stdout and stderr line-by-line as the subprocess
produces them.  As each line passes through, a Pattern Recognition Engine
(regex-based) scans for interactive tokens.  When a match is found, the process
is "soft-locked" (it stays alive but we stop reading) and the system emits a
high-priority event on the EventBus so the panel or dashboard can show the
appropriate widget to the user.

Supported interactive patterns
───────────────────────────────
  Category 1 — y/n confirmation
    [Y/n]  [y/N]  [yes/no]  (y/n)  (Y/N)
    "Do you want to continue?"
    "Continue? [Y/n]"
    "Are you sure?"
    "Proceed?"
    "Overwrite?"

  Category 2 — free-text input
    "Enter passphrase:"    (GPG, SSH keygen)
    "Username:"            (various)
    "Token:"               (gh CLI, etc.)

  Category 3 — password (handled separately by panel_sudo, but
    recognised here so we never deadlock on a sudo sub-invocation)
    "[sudo] password for"
    "Password:"
    "Enter password:"

Usage
─────
    bridge = ProcessBridge(task_id="abc", command="apt install ncdu",
                           cwd="/home/user", password="mypassword")
    ok, output = await bridge.run()

The bridge publishes these events on the EventBus during execution:

  "process_output_chunk"   — a line of stdout/stderr arrived (for live streaming)
  "interactive_prompt"     — a y/n or free-text prompt was detected; process soft-locked
  "interactive_response"   — caller publishes this to send a response; process resumes
  "process_bridge_done"    — process exited (success or failure)
"""
from __future__ import annotations

import asyncio
import logging
import re
from dataclasses import dataclass, field
from typing import Optional

from core.event_bus import bus

log = logging.getLogger("ProcessBridge")

# ── Pattern Recognition Engine ────────────────────────────────────────────────

@dataclass
class _Pattern:
    name:     str
    regex:    re.Pattern
    category: str   # "yn" | "freetext" | "password"
    response_event: str = "interactive_response"


_PATTERNS: list[_Pattern] = [
    # y/n variants
    _Pattern("yn_bracket_upper",   re.compile(r'\[Y/n\]',         re.I), "yn"),
    _Pattern("yn_bracket_lower",   re.compile(r'\[y/N\]',         re.I), "yn"),
    _Pattern("yn_paren",           re.compile(r'\(y(?:es)?/n(?:o)?\)', re.I), "yn"),
    _Pattern("yn_yes_no_bracket",  re.compile(r'\[yes/no\]',      re.I), "yn"),
    _Pattern("yn_continue",        re.compile(r'(?:Do you want to|Want to) continue', re.I), "yn"),
    _Pattern("yn_are_you_sure",    re.compile(r'Are you sure',    re.I), "yn"),
    _Pattern("yn_proceed",         re.compile(r'Proceed\?',       re.I), "yn"),
    _Pattern("yn_overwrite",       re.compile(r'Overwrite\?',     re.I), "yn"),
    _Pattern("yn_install",         re.compile(r'install.*\[Y/n\]',re.I), "yn"),
    # free-text prompts
    _Pattern("freetext_passphrase",re.compile(r'Enter passphrase',re.I), "freetext"),
    _Pattern("freetext_username",  re.compile(r'^Username:\s*$',  re.I), "freetext"),
    _Pattern("freetext_token",     re.compile(r'^Token:\s*$',     re.I), "freetext"),
    # password (handled specially — never write plaintext to log)
    _Pattern("password_sudo",      re.compile(r'\[sudo\] password for', re.I), "password"),
    _Pattern("password_generic",   re.compile(r'^(?:Password|Enter password):\s*$', re.I), "password"),
]


def _classify_line(line: str) -> Optional[_Pattern]:
    """Return the first matching pattern for a line, or None."""
    for p in _PATTERNS:
        if p.regex.search(line):
            return p
    return None


# ── ProcessBridge ─────────────────────────────────────────────────────────────

@dataclass
class ProcessBridge:
    """
    Streams a subprocess and intercepts interactive prompts.

    Parameters
    ──────────
    task_id   — matches the task on the EventBus so UI widgets are scoped
    command   — the shell command string to execute
    cwd       — working directory
    password  — sudo/passphrase pre-filled (written to stdin at start for sudo -S)
    env       — optional extra environment variables
    timeout   — hard timeout in seconds (default 300 = 5 min)
    """
    task_id:  str
    command:  str
    cwd:      str
    password: str           = ""
    env:      dict          = field(default_factory=dict)
    timeout:  float         = 300.0

    # ── Internal state ────────────────────────────────────────────────────────
    _process:       Optional[asyncio.subprocess.Process] = field(default=None, init=False)
    _stdout_lines:  list[str] = field(default_factory=list, init=False)
    _stderr_lines:  list[str] = field(default_factory=list, init=False)
    _soft_locked:   bool      = field(default=False, init=False)

    async def run(self) -> tuple[bool, str]:
        """
        Start the process and stream its output.
        Returns (success, full_output_string).
        """
        log.info("ProcessBridge: starting task=%s cmd=%r cwd=%s", self.task_id, self.command, self.cwd)

        try:
            self._process = await asyncio.create_subprocess_shell(
                self.command,
                stdin  = asyncio.subprocess.PIPE,
                stdout = asyncio.subprocess.PIPE,
                stderr = asyncio.subprocess.PIPE,
                cwd    = self.cwd or None,
            )
        except Exception as exc:
            log.error("ProcessBridge: failed to start process — %s", exc)
            return False, f"Failed to start process: {exc}"

        # If we have a pre-supplied password (sudo -S), write it to stdin
        # immediately.  The process reads it before printing anything, so
        # there is no prompt to detect for the password itself.
        if self.password and "-S" in self.command:
            try:
                self._process.stdin.write((self.password + "\n").encode("utf-8"))
                await self._process.stdin.drain()
                log.debug("ProcessBridge: wrote sudo password to stdin")
            except Exception as exc:
                log.warning("ProcessBridge: failed to write password to stdin — %s", exc)

        # Stream stdout and stderr concurrently
        try:
            await asyncio.wait_for(
                self._stream_both(),
                timeout=self.timeout,
            )
        except asyncio.TimeoutError:
            log.warning("ProcessBridge: task=%s timed out after %.0fs", self.task_id, self.timeout)
            await self._kill()
            return False, f"Process timed out after {self.timeout:.0f}s."

        await self._process.wait()
        exit_code = self._process.returncode

        full_stdout = "\n".join(self._stdout_lines)
        full_stderr = "\n".join(self._stderr_lines)
        output      = full_stdout or full_stderr or ("Done." if exit_code == 0 else f"Exit {exit_code}")
        ok          = exit_code == 0

        bus.publish(
            "process_bridge_done",
            {
                "task_id":   self.task_id,
                "command":   self.command,
                "exit_code": exit_code,
                "success":   ok,
                "stdout":    full_stdout,
                "stderr":    full_stderr,
                "snippet":   (output[:300] + "…") if len(output) > 300 else output,
            },
            source="process_bridge",
        )

        log.info("ProcessBridge: task=%s exit_code=%d", self.task_id, exit_code)
        return ok, output

    async def _stream_both(self) -> None:
        """Read stdout and stderr concurrently until both are exhausted."""
        await asyncio.gather(
            self._stream_reader(self._process.stdout, "stdout"),
            self._stream_reader(self._process.stderr, "stderr"),
        )

    async def _stream_reader(
        self,
        stream: asyncio.StreamReader,
        label:  str,
    ) -> None:
        """
        Read one stream line by line.

        For each line:
          1. Publish process_output_chunk for live display in the panel.
          2. Run the Pattern Recognition Engine.
          3. If a pattern matches → soft-lock → await user response.
        """
        while True:
            try:
                raw = await stream.readline()
            except Exception:
                break
            if not raw:
                break

            line = raw.decode(errors="replace").rstrip("\n")

            # Never log password lines — replace with placeholder
            safe_line = line
            if any(kw in line.lower() for kw in ("password", "passphrase", "token")):
                safe_line = "[sensitive line redacted]"

            # Publish chunk for live streaming to panel output area
            bus.publish(
                "process_output_chunk",
                {
                    "task_id": self.task_id,
                    "line":    line if label != "password" else safe_line,
                    "stream":  label,
                },
                source="process_bridge",
            )

            # Store for final output
            if label == "stdout":
                self._stdout_lines.append(line)
            else:
                self._stderr_lines.append(line)

            # ── Pattern Recognition Engine ─────────────────────────────────
            pattern = _classify_line(line)
            if pattern:
                log.info(
                    "ProcessBridge: interactive prompt detected — pattern='%s' category='%s' line=%r",
                    pattern.name, pattern.category, safe_line,
                )
                response = await self._soft_lock_and_request(line, pattern)
                if response is not None:
                    await self._write_response(response, pattern)

    async def _soft_lock_and_request(
        self,
        prompt_line: str,
        pattern:     _Pattern,
    ) -> Optional[str]:
        """
        Soft-lock: process stays alive but we stop reading and wait for the user.

        Publishes "interactive_prompt" on the bus (high priority via emit).
        Awaits "interactive_response" with matching task_id (120 s timeout).

        Returns the user's response string, or None on timeout/cancel.
        """
        self._soft_locked = True

        # Build human-readable prompt description for the UI
        if pattern.category == "yn":
            prompt_type = "yn"
            ui_message  = f"Command is asking for confirmation:\n{prompt_line.strip()}"
        elif pattern.category == "password":
            prompt_type = "password"
            ui_message  = f"Command requires a password:\n{prompt_line.strip()}"
        else:
            prompt_type = "freetext"
            ui_message  = f"Command requires input:\n{prompt_line.strip()}"

        # High-priority emit (not publish) — goes into priority queue with low number
        asyncio.get_running_loop().create_task(bus.emit(
            "interactive_prompt",
            {
                "task_id":     self.task_id,
                "prompt_type": prompt_type,
                "pattern":     pattern.name,
                "message":     ui_message,
                "prompt_line": prompt_line.strip(),
            },
            source="process_bridge",
        ))

        # Wait for panel/dashboard to publish interactive_response
        loop   = asyncio.get_running_loop()
        fut:   asyncio.Future = loop.create_future()

        def _on_response(event: object) -> None:
            data = getattr(event, "data", {}) or {}
            if data.get("task_id") == self.task_id and not fut.done():
                fut.set_result(data.get("response", ""))

        bus.subscribe("interactive_response", _on_response)
        try:
            response = await asyncio.wait_for(fut, timeout=120.0)
            log.info("ProcessBridge: user responded task=%s response=%r", self.task_id, response)
        except asyncio.TimeoutError:
            log.warning("ProcessBridge: interactive prompt timed out task=%s", self.task_id)
            response = None
            bus.publish(
                "interactive_prompt_timeout",
                {"task_id": self.task_id, "pattern": pattern.name},
                source="process_bridge",
            )
        finally:
            bus._unsubscribe_dead(_on_response)
            self._soft_locked = False

        return response

    async def _write_response(self, response: str, pattern: _Pattern) -> None:
        """Write the user's response to the process stdin."""
        if self._process is None or self._process.stdin is None:
            return
        try:
            payload = (response + "\n").encode("utf-8")
            self._process.stdin.write(payload)
            await self._process.stdin.drain()
            log.debug("ProcessBridge: wrote response to stdin (%d bytes)", len(payload))
        except Exception as exc:
            log.warning("ProcessBridge: failed to write response to stdin — %s", exc)

    async def _kill(self) -> None:
        if self._process and self._process.returncode is None:
            try:
                self._process.kill()
                await self._process.wait()
            except Exception:
                pass