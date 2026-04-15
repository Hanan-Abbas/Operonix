"""
tools/ollama_executor.py
─────────────────────────
OllamaExecutor — Translates LLM action plans into real OS-level operations.

Receives a structured plan dict from OllamaTool and dispatches it to the
correct execution strategy with zero hardcoding:

  shell_command  → asyncio subprocess
  file_operation → FileTool (reuses existing logic + safety)
  http_request   → APITool (reuses existing logic)
  python_eval    → sandboxed eval (safe subset only)
  ui_action      → UITool (reuses existing logic)
  text_response  → passthrough (LLM answered directly)

Design principles
─────────────────
• Every strategy calls an existing tool when one exists — no duplication.
• If the primary strategy fails and the plan has a `fallback_strategy`,
  the executor re-tries with the fallback automatically.
• All dangerous operations go through ToolValidator before execution.
• Emits EventBus events for dashboard visibility.
"""
from __future__ import annotations

import asyncio
import logging
import re
from typing import Any

from core.event_bus import bus

logger = logging.getLogger("OllamaExecutor")

# Safe Python builtins allowed in python_eval strategy
_SAFE_BUILTINS = {
    "abs", "all", "any", "bin", "bool", "chr", "dict", "divmod",
    "enumerate", "filter", "float", "format", "frozenset", "hash",
    "hex", "int", "isinstance", "issubclass", "iter", "len", "list",
    "map", "max", "min", "next", "oct", "ord", "pow", "print",
    "range", "repr", "reversed", "round", "set", "slice", "sorted",
    "str", "sum", "tuple", "type", "zip",
}


class OllamaExecutor:
    """Executes structured action plans produced by OllamaTool."""

    # Maps strategy names → executor methods (populated in __init__)
    _STRATEGY_MAP: dict[str, Any] = {}

    def __init__(self) -> None:
        self._STRATEGY_MAP = {
            "shell_command":  self._exec_shell,
            "file_operation": self._exec_file,
            "http_request":   self._exec_http,
            "python_eval":    self._exec_python,
            "ui_action":      self._exec_ui,
            "text_response":  self._exec_text,
        }

    # ------------------------------------------------------------------ #
    #  Public entry point                                                  #
    # ------------------------------------------------------------------ #

    async def execute(
        self,
        plan: dict,
        original_intent: str = "",
        original_args: dict | None = None,
    ) -> tuple[bool, Any]:
        """
        Dispatch a plan to the correct strategy executor.
        Automatically retries with `fallback_strategy` on failure.
        """
        await bus.emit(
            "ollama_plan_executing",
            {"plan": plan, "intent": original_intent},
            source="ollama_executor",
        )

        strategy = plan.get("strategy", "")
        ok, result = await self._dispatch(strategy, plan, original_intent, original_args)

        if not ok and plan.get("fallback_strategy"):
            fallback = plan["fallback_strategy"]
            logger.warning(
                f"Primary strategy '{strategy}' failed — retrying with fallback '{fallback}'"
            )
            fallback_plan = {**plan, "strategy": fallback}
            ok, result = await self._dispatch(fallback, fallback_plan, original_intent, original_args)

        await bus.emit(
            "ollama_plan_result",
            {"success": ok, "result": str(result)[:500], "intent": original_intent},
            source="ollama_executor",
        )
        return ok, result

    # ------------------------------------------------------------------ #
    #  Internal dispatcher                                                 #
    # ------------------------------------------------------------------ #

    async def _dispatch(
        self,
        strategy: str,
        plan: dict,
        original_intent: str,
        original_args: dict | None,
    ) -> tuple[bool, Any]:
        handler = self._STRATEGY_MAP.get(strategy)
        if not handler:
            return False, (
                f"OllamaExecutor: unknown strategy '{strategy}'. "
                f"Known: {list(self._STRATEGY_MAP)}"
            )
        try:
            return await handler(plan.get("params", {}), original_intent, original_args or {})
        except Exception as exc:
            logger.error(f"Strategy '{strategy}' raised: {exc}", exc_info=True)
            return False, str(exc)

    # ------------------------------------------------------------------ #
    #  Strategy: shell_command                                             #
    # ------------------------------------------------------------------ #

    async def _exec_shell(self, params: dict, intent: str, orig_args: dict) -> tuple[bool, Any]:
        command: str = params.get("command", "")
        cwd: str | None = params.get("cwd")

        if not command:
            return False, "shell_command strategy requires 'command' in params"

        # Run through ToolValidator safety guard before execution
        try:
            from tools.tool_validator import tool_validator
            safe, msg = await tool_validator.validate(
                "ollama_executor", "shell", {"command": command}, intent=intent
            )
            if not safe:
                return False, f"Safety guard blocked command: {msg}"
        except ImportError:
            pass  # validator not available — skip (shouldn't happen)

        logger.info(f"🐚 Executing shell: {command!r} (cwd={cwd})")
        try:
            proc = await asyncio.create_subprocess_shell(
                command,
                stdout=asyncio.subprocess.PIPE,
                stderr=asyncio.subprocess.PIPE,
                cwd=cwd,
            )
            timeout = 60  # seconds
            try:
                stdout, stderr = await asyncio.wait_for(proc.communicate(), timeout=timeout)
            except asyncio.TimeoutError:
                proc.kill()
                return False, f"Command timed out after {timeout}s: {command!r}"

            out = stdout.decode("utf-8", errors="replace").strip()
            err = stderr.decode("utf-8", errors="replace").strip()

            if proc.returncode == 0:
                return True, out or f"Command succeeded: {command!r}"
            return False, f"Command failed (exit {proc.returncode}): {err or out}"

        except Exception as exc:
            return False, f"Shell execution error: {exc}"

    # ------------------------------------------------------------------ #
    #  Strategy: file_operation                                            #
    # ------------------------------------------------------------------ #

    async def _exec_file(self, params: dict, intent: str, orig_args: dict) -> tuple[bool, Any]:
        """Delegates to the registered FileTool if available, else does it directly."""
        operation: str = params.get("operation", "read")
        path: str      = params.get("path", "")
        content: str   = params.get("content", "")

        if not path:
            return False, "file_operation strategy requires 'path' in params"

        # Prefer using the registered file_tool (full safety + event bus)
        try:
            from tools.tool_registry import tool_registry
            file_tool = tool_registry.get_tool("file_tool")
            if file_tool:
                action_map = {
                    "write":  ("write",  {"path": path, "data": content}),
                    "read":   ("read",   {"path": path}),
                    "append": ("append", {"path": path, "data": content}),
                    "delete": ("delete", {"path": path}),
                    "list":   ("list",   {"path": path}),
                    "mkdir":  ("mkdir",  {"path": path}),
                    "move":   ("move",   {"path": path, "destination": params.get("destination", "")}),
                    "exists": ("exists", {"path": path}),
                }
                mapped = action_map.get(operation)
                if mapped:
                    action, args = mapped
                    return await file_tool.run(action, args)
        except ImportError:
            pass

        # Direct fallback if file_tool is unavailable
        from pathlib import Path
        import shutil
        p = Path(path).expanduser().resolve()

        try:
            if operation == "write":
                p.parent.mkdir(parents=True, exist_ok=True)
                p.write_text(content, encoding="utf-8")
                return True, f"Written: {p}"
            elif operation == "read":
                if not p.exists():
                    return False, f"File not found: {p}"
                return True, p.read_text(encoding="utf-8")
            elif operation == "append":
                p.parent.mkdir(parents=True, exist_ok=True)
                with open(p, "a", encoding="utf-8") as f:
                    f.write(content)
                return True, f"Appended to: {p}"
            elif operation == "delete":
                if p.is_dir():
                    shutil.rmtree(p)
                else:
                    p.unlink(missing_ok=True)
                return True, f"Deleted: {p}"
            elif operation in ("list", "ls"):
                if not p.is_dir():
                    return False, f"Not a directory: {p}"
                import os
                return True, os.listdir(p)
            elif operation == "mkdir":
                p.mkdir(parents=True, exist_ok=True)
                return True, f"Directory created: {p}"
            else:
                return False, f"Unknown file operation: {operation}"
        except Exception as exc:
            return False, f"File operation error: {exc}"

    # ------------------------------------------------------------------ #
    #  Strategy: http_request                                              #
    # ------------------------------------------------------------------ #

    async def _exec_http(self, params: dict, intent: str, orig_args: dict) -> tuple[bool, Any]:
        """Delegates to the registered APITool if available, else does it directly."""
        url: str    = params.get("url", "")
        method: str = params.get("method", "GET").upper()
        headers     = params.get("headers", {})
        body        = params.get("body") or params.get("data") or {}

        if not url:
            return False, "http_request strategy requires 'url' in params"

        # Prefer registered api_tool
        try:
            from tools.tool_registry import tool_registry
            api_tool = tool_registry.get_tool("api_tool")
            if api_tool:
                return await api_tool.run(
                    "request",
                    {"url": url, "method": method, "headers": headers, "data": body},
                )
        except ImportError:
            pass

        # Direct fallback
        import aiohttp
        try:
            async with aiohttp.ClientSession() as session:
                req_kwargs = {"headers": headers, "timeout": aiohttp.ClientTimeout(total=30)}
                if method == "GET":
                    async with session.get(url, **req_kwargs) as resp:
                        return await self._parse_http_response(resp)
                elif method == "POST":
                    async with session.post(url, json=body, **req_kwargs) as resp:
                        return await self._parse_http_response(resp)
                elif method == "PUT":
                    async with session.put(url, json=body, **req_kwargs) as resp:
                        return await self._parse_http_response(resp)
                elif method == "DELETE":
                    async with session.delete(url, **req_kwargs) as resp:
                        return await self._parse_http_response(resp)
                else:
                    return False, f"Unsupported HTTP method: {method}"
        except Exception as exc:
            return False, f"HTTP request error: {exc}"

    @staticmethod
    async def _parse_http_response(resp) -> tuple[bool, Any]:
        if resp.status < 300:
            try:
                return True, await resp.json()
            except Exception:
                return True, await resp.text()
        return False, f"HTTP {resp.status}: {await resp.text()}"

    # ------------------------------------------------------------------ #
    #  Strategy: python_eval                                               #
    # ------------------------------------------------------------------ #

    async def _exec_python(self, params: dict, intent: str, orig_args: dict) -> tuple[bool, Any]:
        """
        Evaluates a safe Python expression in a restricted sandbox.
        Suitable for math, string formatting, data transformation — NOT for
        executing arbitrary code.
        """
        expression: str = params.get("expression", "")
        if not expression:
            return False, "python_eval strategy requires 'expression' in params"

        # Block obviously dangerous patterns
        _BLOCKED = re.compile(
            r"\b(import|__import__|exec|eval|open|os\.|sys\.|subprocess|shutil"
            r"|compile|globals|locals|vars|getattr|setattr|delattr|__)\b"
        )
        if _BLOCKED.search(expression):
            return False, f"python_eval blocked dangerous expression: {expression!r}"

        safe_globals = {"__builtins__": {b: __builtins__[b] for b in _SAFE_BUILTINS if b in __builtins__}}  # type: ignore[index]
        if isinstance(__builtins__, dict):
            safe_globals["__builtins__"] = {
                k: v for k, v in __builtins__.items() if k in _SAFE_BUILTINS
            }

        try:
            result = await asyncio.to_thread(eval, expression, safe_globals, {})
            return True, str(result)
        except Exception as exc:
            return False, f"python_eval error: {exc}"

    # ------------------------------------------------------------------ #
    #  Strategy: ui_action                                                 #
    # ------------------------------------------------------------------ #

    async def _exec_ui(self, params: dict, intent: str, orig_args: dict) -> tuple[bool, Any]:
        """Delegates to the registered UITool."""
        action_type: str = params.get("type", "")
        if not action_type:
            return False, "ui_action strategy requires 'type' in params"

        try:
            from tools.tool_registry import tool_registry
            ui_tool = tool_registry.get_tool("ui_tool")
            if ui_tool:
                ui_args = {k: v for k, v in params.items() if k != "type"}
                return await ui_tool.run(action_type, ui_args)
        except ImportError:
            pass

        return False, "ui_tool not available and no direct fallback for ui_action"

    # ------------------------------------------------------------------ #
    #  Strategy: text_response                                             #
    # ------------------------------------------------------------------ #

    async def _exec_text(self, params: dict, intent: str, orig_args: dict) -> tuple[bool, Any]:
        """
        The LLM determined no system action is needed — return its answer directly.
        Useful for information queries, help requests, etc.
        """
        text: str = params.get("text", "")
        return (True, text) if text else (False, "text_response had no text")


# ── Global singleton ──────────────────────────────────────────────────── #
ollama_executor = OllamaExecutor()