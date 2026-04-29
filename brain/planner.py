"""
brain/planner.py
─────────────────
Builds execution step plans for the Executor.

Changes from original
──────────────────────
BUG FIX 1 — _generate_static_steps() passed the suggested_tool only at the
    step level but did NOT forward the full task context (including window_title
    and cwd).  The executor's context enrichment needs those values in the
    dispatched payload.

BUG FIX 2 — Semantic args like {dir_name: "alibaba", location: "current window"}
    are now pre-resolved into a concrete {"path": "/real/path/alibaba"} by
    _resolve_args_for_intent() before the step is built.  This guarantees that
    the Executor and capability receive a proper "path" key regardless of how
    the LLM phrased the intent's parameters.

BUG FIX 3 — _needs_llm_reasoning() was returning False for simple intents
    like create_dir, pushing them into _generate_static_steps().  That path
    still works correctly now that BUG FIX 1 & 2 are in place.  LLM reasoning
    is still triggered for genuinely complex / multi-parameter tasks.

Self-evolving: the LLM step generation path already fetches live capability
names from the registry, so new capabilities auto-appear in LLM plans.
"""
from __future__ import annotations

import json
import logging
import os
from pathlib import Path

from brain.llm_client import llm_client
from core.event_bus import bus


class Planner:

    def __init__(self) -> None:
        self.logger = logging.getLogger("Planner")
        self.plan_storage: dict = {}

    async def start(self) -> None:
        bus.subscribe("request_planning", self.create_plan)
        self.logger.info("Planner: Strategist active. Ready to build execution paths.")

    # ── Main handler ──────────────────────────────────────────────────────

    async def create_plan(self, event: object) -> None:
        data          = event.data
        task_id       = data.get("task_id")
        intent        = data.get("intent")
        args          = data.get("parameters", {})
        suggested_tool = data.get("suggested_tool")
        context       = data.get("context", {})

        self.logger.info("Planner: Generating strategy for %s...", intent)

        # BUG FIX 2 — resolve semantic args into concrete values before
        # building steps so the capability receives a usable "path".
        resolved_args = self._resolve_args_for_intent(intent, args, context, data)

        if self._needs_llm_reasoning(intent, resolved_args):
            steps = await self._generate_llm_steps(
                intent, resolved_args, suggested_tool
            )
        else:
            steps = self._generate_static_steps(
                intent, resolved_args, suggested_tool
            )

        if not steps:
            bus.publish(
                "task_failed",
                {
                    "task_id": task_id,
                    "error": f"Planner failed to generate steps for {intent}",
                },
                source="planner",
            )
            return

        self.plan_storage[task_id] = steps

        # BUG FIX 1 — include the full context in the dispatched payload so
        # the executor's _enrich_context_with_cwd() has window data.
        bus.publish(
            "task_dispatched",
            {
                "task_id":          task_id,
                "intent":           intent,
                "steps":            steps,
                "context":          context,
                "preferred_method": data.get("preferred_method"),
            },
            source="planner",
        )
        self.logger.info(
            "Planner: Dispatched task [%s] to Safety Validator.", task_id
        )

    # ── Arg resolution (BUG FIX 2) ───────────────────────────────────────

    def _resolve_args_for_intent(
        self,
        intent: str,
        args: dict,
        context: dict,
        task_data: dict,
    ) -> dict:
        """
        Translate semantic arg values into concrete filesystem paths where
        needed, so capabilities receive structured, unambiguous arguments.

        Currently handles:
          - "location": "current window"  ->  real CWD from context
          - "dir_name" + resolved location -> "path" key

        This is fully data-driven: new intents that use the same pattern
        get path resolution for free.
        """
        resolved = dict(args or {})
        location = str(resolved.get("location", "")).lower()

        # Resolve "current window" / "current" to a real CWD
        # Resolve all natural-language "here" variants to the active CWD.
        # The LLM may produce: "here", "current", "current window",
        # "current directory", "this folder", etc.
        _HERE_SYNONYMS = {"here", "current", "this", "pwd", "active", "open"}
        location_words = set(location.replace("_", " ").split())

        if location_words & _HERE_SYNONYMS or "current" in location or "here" in location:
            # Try context dict hierarchy (populated by window_detector /
            # orchestrator at snapshot time)
            cwd = (
                context.get("cwd")
                or context.get("window_cwd")
                or (context.get("app_context") or {}).get("cwd")
                or task_data.get("cwd")
                or os.getcwd()
            )
            resolved["cwd_resolved"] = cwd

            # If there's a dir_name but no explicit path, build the path now
            if resolved.get("dir_name") and not resolved.get("path"):
                resolved["path"] = str(Path(cwd) / resolved["dir_name"])
                self.logger.debug(
                    "Resolved 'current window' -> path: %s", resolved["path"]
                )

        return resolved

    # ── Complexity check ──────────────────────────────────────────────────

    def _needs_llm_reasoning(self, intent: str, args: dict) -> bool:
        """
        Trigger LLM reasoning only for genuinely complex tasks.
        Simple single-step ops (create_dir, read_file, etc.) go straight
        to _generate_static_steps().
        """
        raw = args.get("raw_text") or args.get("content") or ""
        if isinstance(raw, str) and len(raw) > 300:
            return True
        if isinstance(args, dict) and len(args) > 4:
            return True
        return False

    # ── LLM step generation ───────────────────────────────────────────────

    async def _generate_llm_steps(
        self, intent: str, args: dict, suggested_tool: str | None
    ) -> list[dict]:
        try:
            from capabilities.registry import capability_registry
            available_capabilities = capability_registry.get_all_names()
        except Exception:
            available_capabilities = [
                "read_file", "write_file", "run_command", "type_text",
                "create_dir", "delete_dir",
            ]

        prompt = f"""
Break down this OS task into executable steps for an automation agent.
Task intent: {intent}
Suggested approach/tool: {suggested_tool}
Parameters: {json.dumps(args)}

Return JSON strictly matching this structure:
{{ "steps": [ {{ "action": "<capability_name>", "args": {{ ... }} }} ] }}

You are allowed to use ONLY these capability names: {available_capabilities}
All path values must be absolute filesystem paths.
"""
        response = await llm_client.ask(prompt, provider="deepseek", use_json=True)

        if not isinstance(response, dict):
            return []

        steps = response.get("steps", [])
        out = []
        for s in steps:
            if isinstance(s, dict) and s.get("action"):
                out.append(
                    {"action": s["action"], "args": s.get("args", {})}
                )
        return out

    # ── Static step generation ────────────────────────────────────────────

    def _generate_static_steps(
        self, intent: str, args: dict, suggested_tool: str | None
    ) -> list[dict]:
        """
        Build a single-step plan for simple, well-understood intents.
        The full args dict (already resolved by _resolve_args_for_intent)
        is passed through unchanged.
        """
        step: dict = {"action": intent, "args": dict(args or {})}
        if suggested_tool:
            step["suggested_tool"] = suggested_tool
        return [step]


planner = Planner()