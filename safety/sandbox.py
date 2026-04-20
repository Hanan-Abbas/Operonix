"""
safety/sandbox.py

Process-isolated sandbox for executing untrusted plugin code.
Each plugin runs in a separate subprocess with:
  - Execution timeout (configurable, default 30s)
  - Memory limit (via resource module on Linux)
  - CPU time limit
  - Kill-switch watchdog for hung processes
  - Structured JSON output capture
  - stderr capture for debugging
"""
from __future__ import annotations

import asyncio
import json
import logging
import os
import signal
import subprocess
import sys
import tempfile
import time
from pathlib import Path
from typing import Any

from core.config import settings

logger = logging.getLogger("Sandbox")

# Defaults — override via settings if needed
DEFAULT_TIMEOUT_SECONDS: int = int(getattr(settings, "SANDBOX_TIMEOUT", 30))
DEFAULT_MEMORY_LIMIT_MB: int = int(getattr(settings, "SANDBOX_MEMORY_MB", 256))
DEFAULT_CPU_LIMIT_SECONDS: int = int(getattr(settings, "SANDBOX_CPU_SECONDS", 20))

_RUNNER_TEMPLATE = '''
import sys
import json
import resource
import traceback

# ── Apply resource limits (Linux only) ──────────────────────────────────────
try:
    mem_bytes = {memory_mb} * 1024 * 1024
    resource.setrlimit(resource.RLIMIT_AS, (mem_bytes, mem_bytes))
    resource.setrlimit(resource.RLIMIT_CPU, ({cpu_seconds}, {cpu_seconds}))
except Exception:
    pass  # Windows / macOS silently skip

# ── Load and execute the plugin ─────────────────────────────────────────────
plugin_path = {plugin_path!r}
context     = {context_json}
args        = {args_json}

try:
    import importlib.util
    spec   = importlib.util.spec_from_file_location("_sandbox_plugin", plugin_path)
    module = importlib.util.module_from_spec(spec)
    spec.loader.exec_module(module)

    # Locate the plugin class (first BasePlugin subclass found)
    plugin_cls = None
    for attr_name in dir(module):
        attr = getattr(module, attr_name)
        if (
            isinstance(attr, type)
            and hasattr(attr, "run")
            and hasattr(attr, "name")
            and attr.__name__ != "BasePlugin"
        ):
            plugin_cls = attr
            break

    if plugin_cls is None:
        raise RuntimeError("No valid plugin class found in module.")

    import asyncio
    plugin_instance = plugin_cls()

    # Validate args before running
    validation_error = plugin_instance.validate(args)
    if validation_error:
        raise ValueError(f"Validation failed: {{validation_error}}")

    result = asyncio.run(plugin_instance.run(context, args))
    output = {{"status": "success", "result": result}}

except Exception as exc:
    output = {{
        "status": "error",
        "error": str(exc),
        "traceback": traceback.format_exc()
    }}

print(json.dumps(output))
'''


class SandboxResult:
    """Structured result from a sandboxed plugin execution."""

    def __init__(
        self,
        status: str,
        result: Any = None,
        error: str | None = None,
        traceback: str | None = None,
        elapsed_ms: int = 0,
        timed_out: bool = False,
    ):
        self.status = status          # "success" | "error" | "timeout"
        self.result = result
        self.error = error
        self.traceback = traceback
        self.elapsed_ms = elapsed_ms
        self.timed_out = timed_out

    @property
    def success(self) -> bool:
        return self.status == "success"

    def to_dict(self) -> dict:
        return {
            "status": self.status,
            "result": self.result,
            "error": self.error,
            "traceback": self.traceback,
            "elapsed_ms": self.elapsed_ms,
            "timed_out": self.timed_out,
        }


class Sandbox:
    """
    Executes plugin files in an isolated subprocess.

    Usage:
        result = await sandbox.run_plugin(
            plugin_path="/path/to/plugin.py",
            context={"active_window": "vscode"},
            args={"query": "hello"},
        )
    """

    def __init__(self):
        self.logger = logging.getLogger("Sandbox")

    async def run_plugin(
        self,
        plugin_path: str,
        context: dict,
        args: dict,
        timeout: int = DEFAULT_TIMEOUT_SECONDS,
        memory_mb: int = DEFAULT_MEMORY_LIMIT_MB,
        cpu_seconds: int = DEFAULT_CPU_LIMIT_SECONDS,
    ) -> SandboxResult:
        """
        Runs a plugin file in a separate subprocess with resource limits.
        Returns a SandboxResult with structured output.
        """
        plugin_path = str(Path(plugin_path).resolve())
        if not os.path.exists(plugin_path):
            return SandboxResult(
                status="error",
                error=f"Plugin file not found: {plugin_path}",
            )

        # Build the runner script dynamically
        runner_code = _RUNNER_TEMPLATE.format(
            plugin_path=plugin_path,
            context_json=json.dumps(context),
            args_json=json.dumps(args),
            memory_mb=memory_mb,
            cpu_seconds=cpu_seconds,
        )

        start_time = time.monotonic()

        try:
            # Write runner to a temp file — avoids shell injection via -c flag
            with tempfile.NamedTemporaryFile(
                mode="w", suffix=".py", delete=False, prefix="sandbox_runner_"
            ) as tmp:
                tmp.write(runner_code)
                runner_path = tmp.name

            process = await asyncio.create_subprocess_exec(
                sys.executable, runner_path,
                stdout=asyncio.subprocess.PIPE,
                stderr=asyncio.subprocess.PIPE,
            )

            try:
                stdout, stderr = await asyncio.wait_for(
                    process.communicate(), timeout=timeout
                )
            except asyncio.TimeoutError:
                # Kill-switch: terminate the hung process
                self._kill_process(process)
                elapsed = int((time.monotonic() - start_time) * 1000)
                self.logger.error(
                    f"Sandbox timeout after {timeout}s for plugin: {plugin_path}"
                )
                return SandboxResult(
                    status="timeout",
                    error=f"Plugin execution timed out after {timeout}s",
                    elapsed_ms=elapsed,
                    timed_out=True,
                )

            elapsed = int((time.monotonic() - start_time) * 1000)

            stdout_text = stdout.decode("utf-8", errors="replace").strip()
            stderr_text = stderr.decode("utf-8", errors="replace").strip()

            if stderr_text:
                self.logger.debug(f"Sandbox stderr for {plugin_path}:\n{stderr_text}")

            if not stdout_text:
                return SandboxResult(
                    status="error",
                    error="Plugin produced no output.",
                    traceback=stderr_text,
                    elapsed_ms=elapsed,
                )

            try:
                output = json.loads(stdout_text)
            except json.JSONDecodeError:
                return SandboxResult(
                    status="error",
                    error=f"Plugin output was not valid JSON: {stdout_text[:200]}",
                    elapsed_ms=elapsed,
                )

            return SandboxResult(
                status=output.get("status", "error"),
                result=output.get("result"),
                error=output.get("error"),
                traceback=output.get("traceback"),
                elapsed_ms=elapsed,
            )

        except Exception as e:
            elapsed = int((time.monotonic() - start_time) * 1000)
            self.logger.error(f"Sandbox execution error: {e}", exc_info=True)
            return SandboxResult(
                status="error",
                error=str(e),
                elapsed_ms=elapsed,
            )
        finally:
            # Clean up temp runner file
            try:
                os.unlink(runner_path)
            except Exception:
                pass

    def _kill_process(self, process: asyncio.subprocess.Process) -> None:
        """Forcefully terminates a subprocess."""
        try:
            process.kill()
            self.logger.warning(f"Killed hung subprocess PID={process.pid}")
        except ProcessLookupError:
            pass  # Already dead
        except Exception as e:
            self.logger.error(f"Failed to kill process: {e}")

    async def run_test_suite(
        self,
        plugin_path: str,
        test_path: str,
        timeout: int = DEFAULT_TIMEOUT_SECONDS,
    ) -> dict:
        """
        Runs a plugin's auto-generated test file via pytest in a subprocess.
        Returns {"passed": bool, "output": str, "return_code": int}
        """
        if not os.path.exists(test_path):
            return {"passed": False, "output": "Test file not found.", "return_code": -1}

        start_time = time.monotonic()
        try:
            process = await asyncio.create_subprocess_exec(
                sys.executable, "-m", "pytest", test_path,
                "--tb=short", "-q", "--no-header",
                stdout=asyncio.subprocess.PIPE,
                stderr=asyncio.subprocess.PIPE,
            )

            try:
                stdout, stderr = await asyncio.wait_for(
                    process.communicate(), timeout=timeout
                )
            except asyncio.TimeoutError:
                self._kill_process(process)
                return {
                    "passed": False,
                    "output": f"Tests timed out after {timeout}s",
                    "return_code": -1,
                    "elapsed_ms": int((time.monotonic() - start_time) * 1000),
                }

            output = stdout.decode("utf-8", errors="replace")
            return {
                "passed": process.returncode == 0,
                "output": output,
                "return_code": process.returncode,
                "elapsed_ms": int((time.monotonic() - start_time) * 1000),
            }

        except Exception as e:
            return {
                "passed": False,
                "output": str(e),
                "return_code": -1,
                "elapsed_ms": int((time.monotonic() - start_time) * 1000),
            }


# Global instance
sandbox = Sandbox()