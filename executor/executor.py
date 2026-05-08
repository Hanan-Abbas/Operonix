"""
executor/executor.py
─────────────────────
Central task executor.

Changes from original
──────────────────────
HYBRID EXECUTION (new):
    Before calling tool.run() for any shell/command action, the executor now
    calls terminal_resolver.resolve() to determine the execution profile
    (Ghost / Bridge / Lab / Ambiguous) and injects it into args["_profile"].

    If the result is AmbiguousTarget (multiple terminals share the same CWD),
    execution is suspended and "target_selection_required" is published on the
    bus.  panel_controller listens to this event and shows the Target Selection
    UI.  When the user picks a terminal, "target_selected" is published back
    and the executor resumes with the chosen BridgeTarget.

    output routing:
    shell_tool publishes "command_output_ready" (Ghost) or "command_dispatched"
    (Bridge/Lab) on the bus.  WebSocket bridge forwards both to the dashboard
    automatically.  The executor also publishes "action_completed" (already
    wired to panel_controller for inline snippet display).

BUG FIX 1 — capability result handling (unchanged from previous version)
BUG FIX 2 — context enrichment before step execution (unchanged)
BUG FIX 3 — preferred_method waterfall tier mapping (unchanged)
"""
from __future__ import annotations

import asyncio
import logging
import os
import platform
import re
import time
from typing import Any

from capabilities.registry import capability_registry
from core.config import settings
from core.error_handler import ErrorHandler
from core.event_bus import bus
from core.terminal_resolver import (
    AmbiguousTarget,
    BridgeTarget,
    GhostTarget,
    LabTarget,
    terminal_resolver,
)
from executor.error_classifier import error_classifier
from executor.fallback_manager import FallbackManager
from executor.focus_manager import FocusManager
from executor.retry_manager import RetryManager
from core.metrics import metrics
from brain.llm_client import llm_client
from tools.tool_registry import tool_registry
from tools.tool_selector import tool_selector

logger = logging.getLogger("Executor")

error_handler = ErrorHandler(event_bus=bus, logger=logger)
retry_manager  = RetryManager()
fallback_manager = FallbackManager()
focus_manager  = FocusManager()

# Canonical waterfall order — do not reorder.
_WATERFALL_ORDER: list[str] = ["plugin", "api", "command", "ui"]

# Map waterfall tier names -> ToolRegistry tool_type strings.
_TIER_TO_TOOL_TYPE: dict[str, str] = {
    "plugin":  "plugin",
    "api":     "api_tool",
    "command": "shell_tool",
    "ui":      "ui_tool",
}

# Actions that go through profile resolution
_SHELL_ACTIONS: frozenset[str] = frozenset({
    "run_command", "execute", "git_op", "check_status",
    "execute_script", "navigate",
})


class Executor:

    def __init__(self) -> None:
        self.os_name = platform.system()
        self.is_running = False
        self.restricted_actions: set[str] = set()

        # Stores suspended tasks waiting for target selection UI response.
        # Structure: { task_id: (step, context, preferred_method, resolve_future) }
        self._pending_target_selections: dict[str, asyncio.Future] = {}

    async def start(self) -> None:
        bus.subscribe("task_safety_cleared", self.execute_plan)
        # Listen for the user's terminal selection from the panel UI
        bus.subscribe("target_selected", self._on_target_selected)
        self.is_running = True
        logger.info("Executor Online | OS: %s", self.os_name)
        logger.info("Tools Loaded: %d", len(tool_registry.list_tools()))

    # ── Main entry point ──────────────────────────────────────────────────

    async def execute_plan(self, event: Any) -> None:
        metrics.total_tasks += 1
        start = time.time()

        task_data = event.data
        task_id   = task_data.get("task_id")
        steps     = task_data.get("steps", [])
        context   = task_data.get("context", {})
        intent    = task_data.get("intent")
        preferred_method: str | None = task_data.get("preferred_method")

        self._enrich_context_with_cwd(context)

        logger.info(
            "Starting Task [%s] with %d steps (preferred=%s)",
            task_id, len(steps), preferred_method or "auto",
        )

        method_used: str = "unknown"

        for step_index, step in enumerate(steps):
            action = step.get("action")
            bus.publish(
                "execution_step_started",
                {"task_id": task_id, "step_index": step_index, "action": action},
                source="executor",
            )

            success, result, step_method = await self._execute_step_safe(
                task_id, step_index, step, context, preferred_method=preferred_method
            )

            if step_method:
                method_used = step_method

            if not success:
                bus.publish(
                    "task_failed",
                    {
                        "task_id":     task_id,
                        "failed_step": step,
                        "error":       result,
                        "intent":      intent,
                        "method_used": method_used,
                    },
                    source="executor",
                )
                retry_manager.clear_task(task_id)
                logger.error(
                    "Task [%s] failed at step %d: %s", task_id, step_index, result
                )
                return

            context["last_result"] = result
            context["last_action"] = action
            bus.publish(
                "execution_step_success",
                {"task_id": task_id, "step_index": step_index, "result": result},
                source="executor",
            )
            logger.info("Step %d completed: %s", step_index, action)

        elapsed = time.time() - start
        metrics.total_duration_seconds += elapsed
        metrics.successful_tasks += 1

        logger.info(
            "Success rate: %.1f%% | Avg duration: %.2fs",
            metrics.success_rate(), metrics.avg_task_duration(),
        )

        bus.publish(
            "task_completed",
            {
                "task_id":     task_id,
                "intent":      intent,
                "steps":       steps,
                "method_used": method_used,
            },
            source="executor",
        )
        retry_manager.clear_task(task_id)
        logger.info(
            "Task [%s] completed successfully (method=%s)", task_id, method_used
        )

    # ── Step execution ─────────────────────────────────────────────────────

    async def _execute_step_safe(
        self,
        task_id: str,
        step_index: int,
        step: dict,
        context: dict,
        preferred_method: str | None = None,
    ) -> tuple[bool, Any, str]:
        action = step.get("action")
        args   = step.get("args", {})

        if action in self.restricted_actions:
            return False, f"Restricted action blocked: {action}", "blocked"

        window_title = context.get("window_title")
        if window_title:
            focused = await focus_manager.ensure_focus(window_title)
            if not focused:
                return (
                    False,
                    f"Failed to focus target window: {window_title}",
                    "focus_failed",
                )

        # ── Hybrid profile resolution ──────────────────────────────────────
        # For shell actions, resolve the execution profile BEFORE the waterfall
        # so shell_tool.run() receives args["_profile"] and doesn't need to
        # re-resolve (avoids a second wmctrl/xdotool round-trip).
        if action in _SHELL_ACTIONS:
            args = await self._resolve_and_inject_profile(
                task_id, step_index, action, args, context
            )
            # _resolve_and_inject_profile returns None if execution must be
            # suspended waiting for user target selection.
            if args is None:
                # Suspend and wait for target_selected event (max 60 s)
                try:
                    chosen_profile = await asyncio.wait_for(
                        self._wait_for_target_selection(task_id),
                        timeout=60.0,
                    )
                except asyncio.TimeoutError:
                    return (
                        False,
                        "Target selection timed out — command cancelled.",
                        "target_selection",
                    )
                # Rebuild args with the user-chosen profile
                args = dict(step.get("args", {}))
                args["_profile"] = chosen_profile
                args["cwd"] = context.get("cwd")

        # ── Direct plugin dispatch (before waterfall) ────────────────────────
        # plugin_registry is separate from capability_registry — the waterfall's
        # "plugin" tier calls tool_selector which doesn't reach plugin instances.
        # We query plugin_registry directly first: find a plugin whose capabilities
        # match the action, validate, and call run(). This is the primary path
        # for all user-generated plugins.
        try:
            from plugins.registry import plugin_registry
            from brain.intent_matcher import match_intent_local

            # Build a map of capability → plugin entry for fast lookup
            cap_map: dict[str, Any] = {}
            for pname, entry in plugin_registry.entries.items():
                caps = getattr(entry.manifest, "capabilities", []) or []
                for cap in caps:
                    cap_map[str(cap).lower().replace("_", " ")] = entry
                # Also match by plugin name itself
                cap_map[pname.replace("_", " ")] = entry

            if cap_map:
                action_normalized = action.lower().replace("_", " ").strip()
                plugin_threshold = float(
                    getattr(settings, "PLUGIN_INTENT_MATCH_THRESHOLD", 0.55)
                )
                matched_cap, score = match_intent_local(
                    action_normalized, list(cap_map.keys()),
                    threshold=plugin_threshold,
                )
                if matched_cap:
                    matched_entry = cap_map[matched_cap]
                    plugin_instance = matched_entry.instance
                    plugin_name = matched_entry.manifest.name

                    # Validate args before calling run()
                    validation_error = plugin_instance.validate(args or {})
                    if validation_error:
                        logger.warning(
                            "Plugin '%s' validation failed: %s — proceeding with defaults.",
                            plugin_name, validation_error,
                        )

                    logger.info(
                        "🔌 Plugin dispatch: '%s' → '%s' (cap='%s', score=%.2f)",
                        action, plugin_name, matched_cap, score,
                    )
                    bus.publish(
                        "tool_selected",
                        {
                            "task_id":    task_id,
                            "step_index": step_index,
                            "tool_type":  "plugin",
                            "tool_name":  plugin_name,
                            "method":     "plugin",
                        },
                        source="executor",
                    )

                    try:
                        plugin_result = await asyncio.wait_for(
                            plugin_instance.run(context, args or {}),
                            timeout=float(getattr(settings, "PLUGIN_TIMEOUT", 60.0)),
                        )
                        if isinstance(plugin_result, dict):
                            status = plugin_result.get("status", "error")
                            if status == "success":
                                return True, plugin_result.get("result"), "plugin"
                            else:
                                # Plugin returned error — log and fall through
                                # to waterfall so other tiers can try
                                logger.warning(
                                    "Plugin '%s' returned error: %s — falling through.",
                                    plugin_name,
                                    plugin_result.get("message", "unknown error"),
                                )
                        else:
                            # Non-dict return — still treat as success
                            return True, plugin_result, "plugin"
                    except asyncio.TimeoutError:
                        logger.warning(
                            "Plugin '%s' timed out after %ss.",
                            plugin_name,
                            getattr(settings, "PLUGIN_TIMEOUT", 60.0),
                        )
                    except Exception as plugin_exc:
                        logger.warning(
                            "Plugin '%s' raised exception: %s — falling through.",
                            plugin_name, plugin_exc,
                        )
        except Exception as exc:
            logger.debug("Plugin dispatch pre-check failed (non-fatal): %s", exc)
        # ─────────────────────────────────────────────────────────────────────

        waterfall = self._build_waterfall(preferred_method)
        tried_tools: list[str] = []
        fallback_attempts = 0
        max_fallbacks = int(getattr(settings, "MAX_RETRY_ATTEMPTS", 3))
        last_error: Any = f"No tool available for action: {action}"
        method_used = "unknown"

        for tier in waterfall:
            if fallback_attempts >= max_fallbacks:
                break

            tool_type_hint = _TIER_TO_TOOL_TYPE.get(tier, tier)

            tool_type, tool_instance = await tool_selector.select_best_tool(
                {"intent": action, "method": tool_type_hint},
                context,
                exclude=tried_tools,
            )

            if not tool_instance:
                logger.debug(
                    "No %s (%s) tool for '%s', trying next tier.",
                    tier, tool_type_hint, action,
                )
                continue

            tool_name = getattr(tool_instance, "name", tool_type)
            tried_tools.append(tool_name)
            method_used = tier

            bus.publish(
                "tool_selected",
                {
                    "task_id":    task_id,
                    "step_index": step_index,
                    "tool_type":  tool_type,
                    "tool_name":  tool_name,
                    "method":     tier,
                },
                source="executor",
            )

            try:
                cap_ok, cap_result = await capability_registry.execute(
                    action, context, args
                )

                if cap_ok and isinstance(cap_result, dict) and "success" in cap_result:
                    if cap_result["success"]:
                        logger.debug(
                            "Capability '%s' executed directly: %s",
                            action, cap_result.get("result"),
                        )
                        return True, cap_result.get("result"), method_used
                    else:
                        last_error = cap_result.get("result", "Capability failed")
                        logger.warning(
                            "Capability '%s' reported failure: %s", action, last_error
                        )
                        return False, last_error, method_used

                if cap_ok and not isinstance(cap_result, dict):
                    return True, cap_result, method_used

                if cap_ok and isinstance(cap_result, dict):
                    cap_intent = cap_result.get("intent") or action
                    cap_args   = cap_result.get("args") or args
                    resolved   = self._resolve_tool_call(cap_intent, cap_args)

                    if resolved:
                        tool_name_r, tool_action, tool_args = resolved
                        tool = tool_registry.get_tool(tool_name_r)
                        if tool:
                            ok, tool_result = await tool.run(tool_action, tool_args)
                            if ok:
                                return True, tool_result, method_used
                            last_error = tool_result
                        else:
                            last_error = f"Tool not registered: {tool_name_r}"
                    else:
                        return True, cap_result, method_used
                else:
                    last_error = cap_result

            except asyncio.TimeoutError:
                last_error = "Execution timed out"
            except Exception as exc:
                last_error = str(exc)
                error_handler.handle_error(
                    exc,
                    component="executor",
                    context={"task_id": task_id, "step": step_index},
                )

            category = await self._classify_error_dynamically(str(last_error))
            should_retry = await retry_manager.should_retry(
                task_id, step_index, error_type=category
            )
            if should_retry:
                logger.info("Retrying step %d (error=%s)", step_index, category)
                continue

            fallback_attempts += 1
            bus.publish(
                "fallback_triggered",
                {"from": tier, "task_id": task_id, "step_index": step_index},
                source="executor",
            )

        return (
            False,
            {"type": "exhausted", "message": last_error, "tried": tried_tools},
            method_used,
        )

    # ── Hybrid profile resolution ──────────────────────────────────────────

    async def _resolve_and_inject_profile(
        self,
        task_id: str,
        step_index: int,
        action: str,
        args: dict,
        context: dict,
    ) -> dict | None:
        """
        Call terminal_resolver.resolve() and inject the result into args.

        Returns:
            dict — args with "_profile" injected (ready to pass to shell_tool)
            None — execution must suspend; AmbiguousTarget was returned and the
                   target_selection_required event has been published.
        """
        cwd          = context.get("cwd") or args.get("cwd")
        command      = args.get("command") or args.get("cmd", "")
        profile_hint = args.get("profile_hint")   # set by intent_parser if known
        venv_path    = args.get("venv_path") or context.get("venv_path")

        profile = await asyncio.get_running_loop().run_in_executor(
            None,
            terminal_resolver.resolve,
            cwd,
            command,
            profile_hint,
            venv_path,
        )

        logger.info(
            "Task [%s] step %d profile resolved: %s",
            task_id, step_index, type(profile).__name__,
        )

        if isinstance(profile, AmbiguousTarget):
            # Publish target_selection_required — panel_controller shows the UI
            bus.publish(
                "target_selection_required",
                {
                    "task_id":    task_id,
                    "step_index": step_index,
                    "command":    command,
                    "candidates": [
                        {
                            "pts_path":     c.pts_path,
                            "window_id":    c.window_id,
                            "window_title": c.window_title,
                            "cwd":          c.cwd,
                        }
                        for c in profile.candidates
                    ],
                },
                source="executor",
            )
            return None   # caller will wait for target_selected

        # Inject resolved profile into a copy of args
        enriched = dict(args)
        enriched["_profile"] = profile
        enriched["cwd"] = cwd
        return enriched

    async def _wait_for_target_selection(self, task_id: str) -> BridgeTarget:
        """
        Return a Future that resolves when the user picks a terminal in the
        panel Target Selection UI.  _on_target_selected() resolves it.
        """
        loop = asyncio.get_running_loop()
        fut: asyncio.Future = loop.create_future()
        self._pending_target_selections[task_id] = fut
        return await fut

    async def _on_target_selected(self, event: Any) -> None:
        """
        EventBus handler for target_selected.
        Published by panel_controller when the user clicks a terminal in the
        Target Selection UI.

        Expected payload:
            {
                "task_id":      str,
                "pts_path":     str,
                "window_id":    str,
                "window_title": str,
                "cwd":          str | None,
            }
        """
        data = event.data if hasattr(event, "data") else event
        task_id = data.get("task_id")

        fut = self._pending_target_selections.pop(task_id, None)
        if fut is None or fut.done():
            logger.warning(
                "target_selected for unknown/already-resolved task: %s", task_id
            )
            return

        chosen = BridgeTarget(
            pts_path     = data.get("pts_path", ""),
            window_id    = data.get("window_id", ""),
            window_title = data.get("window_title", ""),
            cwd          = data.get("cwd"),
        )
        fut.set_result(chosen)
        logger.info(
            "Target selected for task [%s]: %s (%s)",
            task_id, chosen.window_title, chosen.pts_path,
        )

    # ── Context enrichment (BUG FIX 2 — unchanged) ────────────────────────

    @staticmethod
    def _enrich_context_with_cwd(context: dict) -> None:
        if context.get("cwd"):
            return
        if context.get("window_cwd"):
            context["cwd"] = context["window_cwd"]
            return
        app_ctx = context.get("app_context") or {}
        if app_ctx.get("cwd"):
            context["cwd"] = app_ctx["cwd"]
            return
        context["cwd"] = os.getcwd()
        logger.debug("cwd not in context — using process CWD: %s", context["cwd"])

    # ── Waterfall helpers ──────────────────────────────────────────────────

    @staticmethod
    def _build_waterfall(preferred_method: str | None) -> list[str]:
        if preferred_method and preferred_method in _WATERFALL_ORDER:
            idx = _WATERFALL_ORDER.index(preferred_method)
            return _WATERFALL_ORDER[idx:] + _WATERFALL_ORDER[:idx]
        return list(_WATERFALL_ORDER)

    # ── Error classification ───────────────────────────────────────────────

    async def _classify_error_dynamically(self, result: Any) -> str:
        result_str = str(result).lower()
        patterns = {
            "permission_denied": r"(permission|denied|access|forbidden|not allowed)",
            "not_found":         r"(not found|no such|does not exist|404)",
            "timeout":           r"(timeout|timed out|deadline|took too long)",
            "network":           r"(connection|network|unreachable|offline)",
        }
        for category, pattern in patterns.items():
            if re.search(pattern, result_str):
                return category

        prompt = (
            f"Classify this execution error into one category.\n"
            f"Error: {result_str}\n"
            f'Return ONLY JSON: {{"category": "permission_denied|not_found|timeout|network|unknown_error"}}'
        )
        try:
            response = await asyncio.wait_for(
                llm_client.generate(prompt, use_json=True), timeout=2.0
            )
            if isinstance(response, dict):
                return response.get("category", "unknown_error")
        except Exception:
            pass
        return "unknown_error"

    # ── Tool resolution ────────────────────────────────────────────────────

    def _resolve_tool_call(
        self, intent: str, args: dict
    ) -> tuple[str, str, dict] | None:
        try:
            all_tools = tool_registry.get_all_tools()
        except Exception:
            all_tools = {}

        for tool_name, tool_obj in all_tools.items():
            if hasattr(tool_obj, "can_handle") and tool_obj.can_handle(intent):
                return tool_name, intent, args
            if hasattr(tool_obj, "supported_intents") and intent in tool_obj.supported_intents:
                return tool_name, intent, args

        return None


executor = Executor()