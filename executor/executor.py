"""
executor/executor.py
─────────────────────
Central task executor.

CAVEAT 1 FIX — Dual event subscription:
    Executor now subscribes to BOTH:
      • "task_safety_cleared"  — fired by ConfirmationManager after user approves
      • "task_dispatched_safe" — fired by Orchestrator direct flow
    Both channels call execute_plan().  A _seen_task_ids dedup set ensures a
    task arriving on both channels is only executed once.

    profile_hint is extracted by _extract_profile_hint() from wherever it
    lives: top-level key OR steps[0].args["profile_hint"].  It is then
    normalised into every step's args before the waterfall runs.

CAVEAT 3 FIX — Bridge PermissionError self-heal:
    _check_bridge_permission() runs a pre-flight O_WRONLY open on the pts
    device before shell_tool even tries.  On PermissionError or missing pts
    it automatically downgrades to GhostTarget and publishes
    "bridge_permission_denied" to the dashboard with the exact fix command.

HYBRID EXECUTION — unchanged from previous version.
BUG FIX 1, 2, 3  — unchanged from previous version.
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

error_handler    = ErrorHandler(event_bus=bus, logger=logger)
retry_manager    = RetryManager()
fallback_manager = FallbackManager()
focus_manager    = FocusManager()

_WATERFALL_ORDER: list[str] = ["plugin", "api", "command", "ui"]

_TIER_TO_TOOL_TYPE: dict[str, str] = {
    "plugin":  "plugin",
    "api":     "api_tool",
    "command": "shell_tool",
    "ui":      "ui_tool",
}

_SHELL_ACTIONS: frozenset[str] = frozenset({
    "run_command", "execute", "git_op", "check_status",
    "execute_script", "navigate",
})


class Executor:

    def __init__(self) -> None:
        self.os_name = platform.system()
        self.is_running = False
        self.restricted_actions: set[str] = set()
        self._pending_target_selections: dict[str, asyncio.Future] = {}

        # CAVEAT 1 — dedup guard
        # Prevents double-execution when a task arrives on both
        # task_safety_cleared and task_dispatched_safe.
        self._seen_task_ids: set[str] = set()

    async def start(self) -> None:
        # CAVEAT 1 — subscribe to BOTH execution trigger channels
        bus.subscribe("task_safety_cleared",  self.execute_plan)
        bus.subscribe("task_dispatched_safe", self.execute_plan)
        bus.subscribe("target_selected",      self._on_target_selected)
        self.is_running = True
        logger.info("Executor Online | OS: %s", self.os_name)
        logger.info("Tools Loaded: %d", len(tool_registry.list_tools()))

    # ── Main entry point ───────────────────────────────────────────────────

    async def execute_plan(self, event: Any) -> None:
        metrics.total_tasks += 1
        start     = time.time()
        task_data = event.data
        task_id   = task_data.get("task_id")

        # CAVEAT 1 — dedup: skip if already running from the other channel
        if task_id in self._seen_task_ids:
            logger.debug(
                "execute_plan: task [%s] already running — ignoring duplicate "
                "from '%s'.", task_id, getattr(event, "name", "unknown"),
            )
            return
        self._seen_task_ids.add(task_id)

        # BUG 1 FIX — task_safety_cleared carries the full confirmation_required
        # payload.  The executor's steps live at the top level when built by
        # intent_parser, but confirmation_manager re-publishes the whole dict
        # including a nested "full_task" key.  If top-level steps is empty,
        # unwrap full_task.steps as the authoritative fallback so the command
        # is never silently dropped.
        steps = task_data.get("steps") or []
        if not steps:
            full_task = task_data.get("full_task") or {}
            steps = full_task.get("steps") or []
            if steps:
                logger.debug(
                    "Task [%s] steps unwrapped from full_task (%d steps)",
                    task_id, len(steps),
                )

        context          = task_data.get("context") or {}
        intent           = task_data.get("intent")
        preferred_method: str | None = task_data.get("preferred_method")

        self._enrich_context_with_cwd(context)

        # BUG 3 FIX — confirmation path: context may arrive empty because
        # inject_task_metadata (which merges context) is only triggered by
        # capability_mapped, which is never re-emitted after safety clearance.
        # Pull context from full_task if the top-level context is bare.
        if not context.get("cwd"):
            full_task_ctx = (task_data.get("full_task") or {}).get("context") or {}
            if full_task_ctx:
                context.update({k: v for k, v in full_task_ctx.items() if not context.get(k)})
                logger.debug("Task [%s] context enriched from full_task.context", task_id)
            # Last resort: pull cwd directly from full_task parameters
            if not context.get("cwd"):
                params = (task_data.get("full_task") or {}).get("parameters") or {}
                if params.get("cwd"):
                    context["cwd"] = params["cwd"]
            self._enrich_context_with_cwd(context)

        # CAVEAT 1 — normalise profile_hint into every step's args
        # so _resolve_and_inject_profile always finds it via args["profile_hint"]
        # regardless of which event channel delivered the task.
        profile_hint_top = task_data.get("profile_hint")
        if not profile_hint_top and steps:
            profile_hint_top = steps[0].get("args", {}).get("profile_hint")

        if profile_hint_top:
            for step in steps:
                step.setdefault("args", {})
                if "profile_hint" not in step["args"]:
                    step["args"]["profile_hint"] = profile_hint_top

        logger.info(
            "Starting Task [%s] — %d steps | preferred=%s | profile_hint=%s",
            task_id, len(steps),
            preferred_method or "auto",
            profile_hint_top or "auto",
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
                self._seen_task_ids.discard(task_id)
                logger.error("Task [%s] failed at step %d: %s", task_id, step_index, result)
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
        self._seen_task_ids.discard(task_id)
        logger.info("Task [%s] completed (method=%s)", task_id, method_used)

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

        # Skip window focus for plugin actions — background/automation plugins
        # don't need a specific window focused first. Focusing causes failures
        # when the active window title doesn't resolve via wmctrl/xdotool.
        _SKIP_FOCUS_FOR_METHODS = {"plugin"}
        _needs_focus = preferred_method not in _SKIP_FOCUS_FOR_METHODS
        window_title = context.get("window_title")
        if window_title and _needs_focus:
            focused = await focus_manager.ensure_focus(window_title)
            if not focused:
                return (
                    False,
                    f"Failed to focus target window: {window_title}",
                    "focus_failed",
                )

        # ── Hybrid profile resolution ──────────────────────────────────────
        if action in _SHELL_ACTIONS:
            args = await self._resolve_and_inject_profile(
                task_id, step_index, action, args, context
            )
            if args is None:
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
                args = dict(step.get("args", {}))
                args["_profile"] = chosen_profile
                args["cwd"]      = context.get("cwd")

        # ── Direct plugin dispatch ─────────────────────────────────────────
        try:
            from plugins.registry import plugin_registry
            from brain.intent_matcher import match_intent_local

            cap_map: dict[str, Any] = {}
            for pname, entry in plugin_registry.entries.items():
                caps = getattr(entry.manifest, "capabilities", []) or []
                for cap in caps:
                    cap_map[str(cap).lower().replace("_", " ")] = entry
                cap_map[pname.replace("_", " ")] = entry

            if cap_map:
                action_normalized = action.lower().replace("_", " ").strip()
                plugin_threshold  = float(getattr(settings, "PLUGIN_INTENT_MATCH_THRESHOLD", 0.55))
                matched_cap, score = match_intent_local(
                    action_normalized, list(cap_map.keys()), threshold=plugin_threshold,
                )
                if matched_cap:
                    matched_entry   = cap_map[matched_cap]
                    plugin_instance = matched_entry.instance
                    plugin_name     = matched_entry.manifest.name

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
                            if plugin_result.get("status") == "success":
                                return True, plugin_result.get("result"), "plugin"
                            else:
                                logger.warning(
                                    "Plugin '%s' returned error: %s — falling through.",
                                    plugin_name, plugin_result.get("message", "unknown"),
                                )
                        else:
                            return True, plugin_result, "plugin"
                    except asyncio.TimeoutError:
                        logger.warning("Plugin '%s' timed out.", plugin_name)
                    except Exception as plugin_exc:
                        logger.warning("Plugin '%s' exception: %s — falling through.", plugin_name, plugin_exc)
        except Exception as exc:
            logger.debug("Plugin dispatch pre-check failed (non-fatal): %s", exc)

        # ── Waterfall ──────────────────────────────────────────────────────
        waterfall       = self._build_waterfall(preferred_method)
        tried_tools: list[str] = []
        fallback_attempts = 0
        max_fallbacks   = int(getattr(settings, "MAX_RETRY_ATTEMPTS", 3))
        last_error: Any = f"No tool available for action: {action}"
        method_used     = "unknown"

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
                logger.debug("No %s tool for '%s', trying next tier.", tier, action)
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
                cap_ok, cap_result = await capability_registry.execute(action, context, args)

                if cap_ok and isinstance(cap_result, dict) and "success" in cap_result:
                    if cap_result["success"]:
                        return True, cap_result.get("result"), method_used
                    else:
                        last_error = cap_result.get("result", "Capability failed")
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
                    exc, component="executor",
                    context={"task_id": task_id, "step": step_index},
                )

            category     = await self._classify_error_dynamically(str(last_error))
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
        cwd          = context.get("cwd") or args.get("cwd")
        command      = args.get("command") or args.get("cmd", "")
        profile_hint = args.get("profile_hint")
        venv_path    = args.get("venv_path") or context.get("venv_path")

        profile = await asyncio.get_running_loop().run_in_executor(
            None, terminal_resolver.resolve, cwd, command, profile_hint, venv_path,
        )

        logger.info(
            "Task [%s] step %d → %s", task_id, step_index, type(profile).__name__,
        )

        # CAVEAT 3 — Bridge pre-flight permission check
        if isinstance(profile, BridgeTarget):
            profile = await asyncio.get_running_loop().run_in_executor(
                None, self._check_bridge_permission, profile, task_id, cwd,
            )

        if isinstance(profile, AmbiguousTarget):
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
            return None

        enriched = dict(args)
        enriched["_profile"] = profile
        enriched["cwd"]      = cwd
        return enriched

    @staticmethod
    def _check_bridge_permission(
        profile: BridgeTarget,
        task_id: str,
        cwd: str | None,
    ) -> BridgeTarget | GhostTarget:
        """
        CAVEAT 3 — pre-flight pts write permission check (runs in thread executor).

        Opens the pts device O_WRONLY|O_NOCTTY and closes it immediately.
        On PermissionError → downgrade to Ghost + publish dashboard warning.
        On missing device → same fallback.
        """
        pts = profile.pts_path

        if not pts or not os.path.exists(pts):
            logger.warning("Bridge pre-flight: pts '%s' missing — downgrading to Ghost", pts)
            bus.publish(
                "bridge_permission_denied",
                {
                    "task_id":       task_id,
                    "pts":           pts,
                    "reason":        "pts device not found",
                    "fallback":      "ghost",
                    "action_needed": "Check that the target terminal is still open.",
                },
                source="executor",
            )
            return GhostTarget(cwd=cwd)

        try:
            fd = os.open(pts, os.O_WRONLY | os.O_NOCTTY)
            os.close(fd)
            logger.debug("Bridge pre-flight: pts '%s' writable ✓", pts)
            return profile

        except PermissionError:
            logger.warning(
                "Bridge pre-flight: PermissionError on '%s' — downgrading to Ghost. "
                "Fix: echo 0 | sudo tee /proc/sys/kernel/yama/ptrace_scope",
                pts,
            )
            bus.publish(
                "bridge_permission_denied",
                {
                    "task_id":       task_id,
                    "pts":           pts,
                    "reason":        "ptrace_scope hardening blocks pts write",
                    "fallback":      "ghost",
                    "action_needed": (
                        "Run setup.sh OR manually: "
                        "echo 0 | sudo tee /proc/sys/kernel/yama/ptrace_scope"
                    ),
                },
                source="executor",
            )
            return GhostTarget(cwd=cwd)

        except OSError as exc:
            logger.warning("Bridge pre-flight: OSError on '%s': %s — Ghost", pts, exc)
            bus.publish(
                "bridge_permission_denied",
                {
                    "task_id":  task_id,
                    "pts":      pts,
                    "reason":   f"OS error: {exc}",
                    "fallback": "ghost",
                },
                source="executor",
            )
            return GhostTarget(cwd=cwd)

    async def _wait_for_target_selection(self, task_id: str) -> BridgeTarget:
        loop = asyncio.get_running_loop()
        fut: asyncio.Future = loop.create_future()
        self._pending_target_selections[task_id] = fut
        return await fut

    async def _on_target_selected(self, event: Any) -> None:
        data    = event.data if hasattr(event, "data") else event
        task_id = data.get("task_id")
        fut     = self._pending_target_selections.pop(task_id, None)
        if fut is None or fut.done():
            logger.warning("target_selected for unknown task: %s", task_id)
            return
        chosen = BridgeTarget(
            pts_path     = data.get("pts_path", ""),
            window_id    = data.get("window_id", ""),
            window_title = data.get("window_title", ""),
            cwd          = data.get("cwd"),
        )
        fut.set_result(chosen)
        logger.info("Target selected [%s]: %s (%s)", task_id, chosen.window_title, chosen.pts_path)

    # ── Context enrichment ─────────────────────────────────────────────────

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

    def _resolve_tool_call(self, intent: str, args: dict) -> tuple[str, str, dict] | None:
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