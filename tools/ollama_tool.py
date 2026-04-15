"""
tools/ollama_tool.py
─────────────────────
Ollama LLM Fallback Tool — last-resort execution layer for Operonix.

When no registered native tool can handle an intent, this tool:
  1. Sends the intent + args to a local Ollama model.
  2. Receives a structured action plan (JSON) from the model.
  3. Dispatches the plan to OllamaExecutor, which performs the real
     OS-level action (file write, shell command, HTTP call, etc.)
     without hardcoding anything — the LLM decides the strategy.

Priority: 10 (lowest) — only runs when everything else fails.

Self-evolving hook
──────────────────
Emits "ollama_fallback_used" on the EventBus so the learning system
can track which intents keep hitting the LLM and prompt the developer
to build a native tool for them.
"""
from __future__ import annotations

import json
import logging
from typing import Any

import aiohttp

from core.event_bus import bus
from core.config import settings

logger = logging.getLogger("OllamaTool")


class OllamaTool:
    name = "ollama_tool"
    tool_type = "ollama_tool"

    # Explicitly declares that it can handle *any* intent — acts as a
    # universal catch-all. The registry checks can_handle() first, so
    # native tools with higher priority still win.
    _CATCH_ALL = True

    def can_handle(self, intent: str) -> bool:
        """Always returns True — this tool is the universal fallback."""
        return True

    # ------------------------------------------------------------------ #
    #  Main entry point                                                    #
    # ------------------------------------------------------------------ #

    async def run(self, action: str, args: dict) -> tuple[bool, Any]:
        """
        Translate intent + args into a real action via Ollama LLM.

        Parameters
        ──────────
        action : the intent string (e.g. "write_file", "run_command", …)
        args   : original args dict from the planner

        Returns
        ───────
        (True, result_message) on success
        (False, error_message) on failure
        """
        await bus.emit(
            "ollama_fallback_used",
            {"intent": action, "args": args},
            source="ollama_tool",
        )
        logger.info(f"🤖 OllamaTool handling intent='{action}' via LLM fallback")

        # 1. Ask the LLM how to fulfil this intent
        ok, plan_or_err = await self._query_ollama(action, args)
        if not ok:
            return False, f"OllamaTool LLM query failed: {plan_or_err}"

        # 2. Execute the plan using OllamaExecutor
        from tools.ollama_executor import ollama_executor  # lazy to avoid circular
        return await ollama_executor.execute(plan_or_err, original_intent=action, original_args=args)

    # ------------------------------------------------------------------ #
    #  Ollama LLM query                                                    #
    # ------------------------------------------------------------------ #

    async def _query_ollama(self, intent: str, args: dict) -> tuple[bool, Any]:
        """
        Sends a structured prompt to Ollama and returns a parsed action plan.
        """
        model   = getattr(settings, "OLLAMA_MODEL",   "llama3")
        base_url = getattr(settings, "OLLAMA_BASE_URL", "http://localhost:11434")
        timeout  = getattr(settings, "OLLAMA_TIMEOUT",  30)

        prompt = self._build_prompt(intent, args)

        payload = {
            "model":  model,
            "prompt": prompt,
            "stream": False,
            "format": "json",   # Ollama native JSON mode (models that support it)
        }

        try:
            async with aiohttp.ClientSession() as session:
                async with session.post(
                    f"{base_url}/api/generate",
                    json=payload,
                    timeout=aiohttp.ClientTimeout(total=timeout),
                ) as resp:
                    if resp.status != 200:
                        text = await resp.text()
                        return False, f"Ollama HTTP {resp.status}: {text}"
                    data = await resp.json()

            raw_text: str = data.get("response", "")
            return self._parse_plan(raw_text)

        except aiohttp.ClientConnectorError:
            return False, (
                "Cannot connect to Ollama. "
                "Is it running? (ollama serve)"
            )
        except Exception as exc:
            return False, str(exc)

    # ------------------------------------------------------------------ #
    #  Prompt construction                                                 #
    # ------------------------------------------------------------------ #

    def _build_prompt(self, intent: str, args: dict) -> str:
        """
        Builds a tightly-scoped system prompt so the LLM always returns
        a valid, executable JSON action plan — no prose, no hallucination.
        """
        args_json = json.dumps(args, indent=2, default=str)

        # Dynamically load the list of known intents so the model is aware
        # of the full capability surface without hardcoding.
        try:
            from tools.tool_registry import tool_registry
            known_intents = ", ".join(
                sorted({
                    i
                    for entry in tool_registry._entries.values()
                    for i in getattr(entry.instance, "supported_intents", [])
                })
            )
        except Exception:
            known_intents = "unknown"

        return f"""You are the execution engine of Operonix, a local AI operating system agent.

Your ONLY job is to return a valid JSON action plan for the given intent.
Do NOT explain anything. Do NOT add prose. Return ONLY a JSON object.

== INTENT ==
{intent}

== ARGS ==
{args_json}

== KNOWN NATIVE INTENTS (for reference) ==
{known_intents}

== OUTPUT FORMAT ==
Return a JSON object with this exact schema:

{{
  "strategy": "<one of: shell_command | file_operation | http_request | python_eval | ui_action | text_response>",
  "action": "<specific action within the strategy>",
  "params": {{
    // all parameters needed to execute the action
  }},
  "fallback_strategy": "<optional: alternative strategy if primary fails>",
  "explanation": "<one sentence: what this plan does>"
}}

== STRATEGY GUIDE ==
- shell_command  : run a terminal command. params: {{"command": str, "cwd": str|null}}
- file_operation : read/write/delete/list a file or dir. params: {{"operation": str, "path": str, "content": str|null}}
- http_request   : make an HTTP call. params: {{"url": str, "method": str, "headers": dict, "body": dict|null}}
- python_eval    : evaluate safe Python expressions (math, formatting, etc). params: {{"expression": str}}
- ui_action      : send keyboard/mouse actions. params: {{"type": str, "value": str|null, "x": int|null, "y": int|null}}
- text_response  : return a text answer (no system action needed). params: {{"text": str}}

Respond with ONLY the JSON object. No markdown. No backticks.
"""

    # ------------------------------------------------------------------ #
    #  Response parsing                                                    #
    # ------------------------------------------------------------------ #

    def _parse_plan(self, raw: str) -> tuple[bool, Any]:
        """
        Safely parses the LLM's JSON response.
        Strips markdown fences if the model adds them anyway.
        """
        cleaned = raw.strip()
        # Some models wrap JSON in ```json … ``` even when asked not to
        if cleaned.startswith("```"):
            lines = cleaned.splitlines()
            cleaned = "\n".join(
                l for l in lines
                if not l.strip().startswith("```")
            ).strip()

        try:
            plan = json.loads(cleaned)
        except json.JSONDecodeError:
            # Last resort: extract first {...} block
            import re
            match = re.search(r"\{.*\}", cleaned, re.DOTALL)
            if match:
                try:
                    plan = json.loads(match.group())
                except Exception:
                    return False, f"Could not parse LLM response as JSON: {cleaned[:200]}"
            else:
                return False, f"LLM returned no JSON: {cleaned[:200]}"

        required = {"strategy", "action", "params"}
        missing = required - plan.keys()
        if missing:
            return False, f"LLM plan missing required keys: {missing}"

        return True, plan


# ── Global singleton ──────────────────────────────────────────────────── #
ollama_tool = OllamaTool()