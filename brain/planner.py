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

REFLECTOR INTEGRATION — _best_method_from_reflector():
    When no preferred_method arrives from the intent_parser, the Planner now
    consults the Reflector's persisted per-tier confidence scores (stored in
    LongTermMemory) and selects the tier with the highest learned score as the
    preferred_method hint for the Executor waterfall.  This closes the self-
    evolution loop: consecutive failures on a tier lower its confidence and
    automatically route future tasks around it.

    RISK MITIGATIONS:
      R1 — _best_method_from_reflector() is fully wrapped in try/except; any
           LongTermMemory I/O failure returns None (no preferred_method), so
           the waterfall falls back to its default order safely.
      R2 — A _SIGNAL_THRESHOLD of 0.10 pp above the default (0.75) is
           required before acting on a score, preventing noise from early
           one-off failures causing premature re-routing.
      R3 — LongTermMemory is instantiated fresh per call (lightweight), so
           there is no shared mutable state between planning calls.
      R4 — preferred_method is ONLY overridden when the caller did NOT
           already supply one; explicit caller choices always win.

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

        # ── REFLECTOR INTEGRATION: Reflector-informed preferred_method ─────
        # If the caller (intent_parser / panel) already specified a preferred
        # method, respect it unconditionally (RISK R4 — explicit wins).
        # Otherwise, query the Reflector's persisted confidence scores to pick
        # the tier most likely to succeed for this intent on this app.
        # RISK R1: _best_method_from_reflector() is fully guarded; returns
        #   None on any failure so we never block or raise here.
        preferred_method = data.get("preferred_method")
        if not preferred_method:
            preferred_method = self._best_method_from_reflector(intent)
            if preferred_method:
                self.logger.info(
                    "Planner: Reflector suggests preferred_method='%s' for intent='%s'",
                    preferred_method, intent,
                )

        # BUG FIX 1 — include the full context in the dispatched payload so
        # the executor's _enrich_context_with_cwd() has window data.
        bus.publish(
            "task_dispatched",
            {
                "task_id":          task_id,
                "intent":           intent,
                "steps":            steps,
                "context":          context,
                "preferred_method": preferred_method,
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
        Translate semantic arg values into concrete values where needed, so
        capabilities and plugins receive structured, unambiguous arguments.

        Handles:
          - "location": "current window"  →  real CWD from context
          - "dir_name" + resolved location → "path" key
          - app_opening: ensures app_name is always populated, extracting it
            from 'command', positional 'args', or 'target' if missing.
            This is the last-resort contract normalizer before the planner
            emits the step — it means the executor's _normalize_args_for_plugin
            never has to guess for app_opening tasks.
        """
        resolved = dict(args or {})

        # ── CWD / location resolution ─────────────────────────────────────────
        location = str(resolved.get("location", "")).lower()
        _HERE_SYNONYMS = {"here", "current", "this", "pwd", "active", "open"}
        location_words = set(location.replace("_", " ").split())

        if location_words & _HERE_SYNONYMS or "current" in location or "here" in location:
            cwd = (
                context.get("cwd")
                or context.get("window_cwd")
                or (context.get("app_context") or {}).get("cwd")
                or task_data.get("cwd")
                or os.getcwd()
            )
            resolved["cwd_resolved"] = cwd

            if resolved.get("dir_name") and not resolved.get("path"):
                resolved["path"] = str(Path(cwd) / resolved["dir_name"])
                self.logger.debug(
                    "Resolved 'current window' -> path: %s", resolved["path"]
                )

        # ── Manifest-driven arg normalization ────────────────────────────────
        # Each plugin declares parameter_schema in its manifest listing the args
        # it needs and their aliases. If any required arg is missing from the
        # resolved dict, we try to fill it from positional args, command field,
        # or common alias keys — fully driven by the manifest, zero hardcoding.
        resolved = self._fill_missing_args_from_manifest(intent, resolved)

        return resolved

    def _fill_missing_args_from_manifest(self, intent: str, args: dict) -> dict:
        """
        Uses the matched plugin's parameter_schema to fill any missing required
        args from the positional 'args' list, 'command' field, or alias keys.

        This replaces the hardcoded app_opening block — it works for ANY plugin
        that declares its parameter_schema in the manifest.
        """
        try:
            from plugins.loader import plugin_loader
            from plugins.manifest_schema import PluginManifest

            intent_normalized = intent.lower().replace("_", " ").strip()
            matched_manifest: PluginManifest | None = None

            for entry in plugin_loader.list_plugins():
                plugin_dir = entry.get("plugin_dir", "")
                if not plugin_dir:
                    continue
                m = PluginManifest.load(plugin_dir)
                if not m or not m.parameter_schema:
                    continue
                caps = [c.lower().replace("_", " ").strip() for c in (m.capabilities or [])]
                intent_field = m.intent.lower().replace("_", " ").strip()
                if intent_normalized in caps or intent_normalized == intent_field:
                    matched_manifest = m
                    break

            if not matched_manifest:
                return args

            resolved = dict(args)
            positional = resolved.get("args", [])
            if not isinstance(positional, list):
                positional = []
            cmd = str(resolved.get("command", "")).strip()
            _LAUNCH_VERBS = {"open", "launch", "start", "run", "execute"}

            for param in matched_manifest.parameter_schema:
                p_name    = param.get("name", "")
                p_required = param.get("required", False)
                p_aliases  = param.get("aliases", [])

                if not p_name or resolved.get(p_name):
                    continue  # already present

                # Try aliases first
                value = None
                for alias in p_aliases:
                    if resolved.get(alias):
                        value = resolved[alias]
                        break

                # Try positional args[0]
                if value is None and positional:
                    value = str(positional[0]).strip() or None

                # Try command field: if it's a verb+target, take the target
                if value is None and cmd:
                    parts = cmd.split()
                    if len(parts) == 2 and parts[0].lower() in _LAUNCH_VERBS:
                        value = parts[1]
                    elif len(parts) == 1 and parts[0].lower() not in _LAUNCH_VERBS:
                        value = parts[0]

                if value:
                    resolved[p_name] = value
                    self.logger.debug(
                        "Planner filled '%s'='%s' for intent='%s' via manifest schema",
                        p_name, value, intent,
                    )
                elif p_required:
                    self.logger.warning(
                        "Planner: could not resolve required arg '%s' for intent='%s' "
                        "from args=%s", p_name, intent, args,
                    )

            return resolved

        except Exception as exc:
            self.logger.debug("_fill_missing_args_from_manifest failed (non-fatal): %s", exc)
            return args

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

    # ── Reflector-informed routing ────────────────────────────────────────

    def _best_method_from_reflector(self, intent: str) -> str | None:
        """
        Queries the Reflector's persisted capability-confidence scores from
        LongTermMemory and returns the tier with the highest learned score,
        but ONLY when that score is meaningfully above the uninformed default
        (0.75).  This prevents a single noisy success/failure from prematurely
        re-routing tasks.

        Tiers checked: plugin → api → command → ui  (waterfall order)

        Returns
        -------
        str | None
            The preferred capability tier name, or None if no reliable signal
            exists (scores too close, LTM unavailable, or first-run).

        Risk mitigations
        ----------------
        R1 — entire method is wrapped in try/except; any I/O or import failure
             returns None without raising, so the planner always proceeds.
        R2 — _SIGNAL_THRESHOLD (0.10 pp above default) filters noise from
             early one-off events on a cold confidence store.
        R3 — LongTermMemory is instantiated fresh (not cached on self) so
             there is no shared mutable state that could skew concurrent plans.
        R4 — called only when preferred_method is not already set by caller;
             explicit caller intent always wins (enforced in create_plan).
        """
        _TIERS            = ["plugin", "api", "command", "ui"]
        _DEFAULT_SCORE    = 0.75
        _SIGNAL_THRESHOLD = 0.10   # must be ≥10 pp above default to act

        try:
            from memory.long_term_memory import LongTermMemory  # lazy import (R1, R3)
            ltm = LongTermMemory()
            scores: dict[str, float] = {
                tier: ltm.get_float(f"confidence:{tier}", default=_DEFAULT_SCORE)
                for tier in _TIERS
            }

            best_tier  = max(scores, key=lambda t: scores[t])
            best_score = scores[best_tier]

            if best_score >= _DEFAULT_SCORE + _SIGNAL_THRESHOLD:
                self.logger.debug(
                    "Planner._best_method_from_reflector: '%s' score=%.3f for intent='%s'",
                    best_tier, best_score, intent,
                )
                return best_tier

            self.logger.debug(
                "Planner._best_method_from_reflector: no strong signal "
                "(best=%s score=%.3f, threshold=%.3f) — using default waterfall.",
                best_tier, best_score, _DEFAULT_SCORE + _SIGNAL_THRESHOLD,
            )

        except Exception as exc:
            # R1 — never raise from here; degrade gracefully.
            self.logger.debug(
                "Planner._best_method_from_reflector failed (non-fatal): %s", exc
            )

        return None


planner = Planner()