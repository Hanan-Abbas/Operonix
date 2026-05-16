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

CAVEAT 4 FIX — Output truncation:
    Large command stdout (e.g. ls -la on a home directory with hundreds of
    bash_history tmp files) flooded the panel event bus with thousands of
    characters.  truncate_output() from safety.risk_rules is now called on
    every string result before it is published to execution_step_success,
    capping output at 2000 chars / 50 lines with a notice appended.
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
from safety.risk_rules import truncate_output

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
        self._seen_task_ids: set[str] = set()

        # ── Concurrent execution registry ──────────────────────────────────
        # Maps task_id → asyncio.Task so running tasks can be inspected,
        # cancelled, or awaited.  Tasks are removed on completion/failure.
        # The event loop is never blocked — each task runs concurrently so
        # new panel inputs and bus events are processed even during downloads.
        self._running_tasks: dict[str, asyncio.Task] = {}

    async def start(self) -> None:
        # SUBSCRIPTION ARCHITECTURE FIX:
        #
        # The execution trigger chain is:
        #
        #   safety_validator   → task_safety_cleared (low-risk, no confirmation needed)
        #                      → confirmation_required (high-risk, needs user approval)
        #   confirmation_manager → task_safety_cleared (after user clicks Allow)
        #   orchestrator.handle_safety_cleared → task_dispatched_safe (enriches + re-emits)
        #
        # PROBLEM: subscribing executor to "task_safety_cleared" caused it to run
        # on the safety_validator's event BEFORE the user confirmed anything.
        # The safety_validator publishes task_safety_cleared for low-risk tasks,
        # but for HIGH-RISK tasks it publishes confirmation_required — then
        # confirmation_manager pauses and waits.  However the orchestrator ALSO
        # subscribes to task_safety_cleared and re-emits task_dispatched_safe
        # immediately.  The executor on task_safety_cleared fired on that first
        # safety_validator event before the user saw any confirmation dialog.
        #
        # FIX: executor subscribes ONLY to "task_dispatched_safe".
        #   • Low-risk path:  safety_validator → task_safety_cleared
        #                     → orchestrator.handle_safety_cleared → task_dispatched_safe
        #                     → executor (correct, no confirmation needed)
        #   • High-risk path: safety_validator → confirmation_required
        #                     → [user approves] → confirmation_manager → task_safety_cleared
        #                     → orchestrator.handle_safety_cleared → task_dispatched_safe
        #                     → executor (correct, AFTER user approval)
        #
        # This single change ensures the executor ALWAYS runs after orchestrator
        # has enriched the payload with context + profile_hint, AND always runs
        # after user confirmation for high-risk tasks.
        bus.subscribe("task_dispatched_safe", self.execute_plan)
        bus.subscribe("target_selected",      self._on_target_selected)
        self.is_running = True
        logger.info("Executor Online | OS: %s", self.os_name)
        logger.info("Tools Loaded: %d", len(tool_registry.list_tools()))

    # ── Main entry point ───────────────────────────────────────────────────

    async def execute_plan(self, event: Any) -> None:
        """
        Receive a task and dispatch it as a background asyncio.Task.

        CONCURRENCY FIX:
        Previously execute_plan() awaited _run_plan() directly. This held the
        entire executor coroutine — and therefore the event loop — blocked for
        the full duration of the command (seconds to minutes for downloads).
        No new panel input, bus event, or health poll could be processed.

        Fix: wrap _run_plan() in asyncio.create_task(). The task runs
        concurrently on the same event loop without blocking it. The panel
        remains fully responsive while apt installs, files download, or
        pytest runs in the background.

        Multiple tasks can run simultaneously (e.g. a ghost background task
        while waiting for a sudo password on another). Each has its own
        task_id so bus events, dedup, and the running_tasks registry are
        all cleanly scoped.
        """
        task_data = event.data
        task_id   = task_data.get("task_id")

        # Dedup guard — same task arriving on both channels
        if task_id in self._seen_task_ids:
            logger.debug(
                "execute_plan: task [%s] already running — ignoring duplicate from '%s'.",
                task_id, getattr(event, "name", "unknown"),
            )
            return
        self._seen_task_ids.add(task_id)

        # Wrap in a Task so the event loop stays free
        task = asyncio.create_task(
            self._run_plan(event),
            name=f"operonix-task-{task_id}",
        )
        self._running_tasks[task_id] = task

        def _on_done(t: asyncio.Task) -> None:
            self._running_tasks.pop(task_id, None)
            # Keep task_id in _seen_task_ids for 5 s after completion to absorb
            # any late-arriving duplicate events (e.g. orchestrator re-emitting
            # task_dispatched_safe on a different code path).
            asyncio.get_event_loop().call_later(
                5.0, self._seen_task_ids.discard, task_id
            )
            if t.cancelled():
                logger.info("Task [%s] was cancelled.", task_id)
            elif t.exception():
                logger.error("Task [%s] raised: %s", task_id, t.exception())

        task.add_done_callback(_on_done)

        # Publish immediately so dashboard shows the task as queued/running
        bus.publish(
            "task_queued",
            {
                "task_id":        task_id,
                "concurrent_count": len(self._running_tasks),
            },
            source="executor",
        )
        logger.info(
            "Task [%s] dispatched as background task (%d running).",
            task_id, len(self._running_tasks),
        )

    async def _run_plan(self, event: Any) -> None:
        """
        The actual execution logic — runs inside a background Task.
        Identical to the previous execute_plan() body.
        """
        metrics.total_tasks += 1
        start     = time.time()
        task_data = event.data
        task_id   = task_data.get("task_id")

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
                logger.error("Task [%s] failed at step %d: %s", task_id, step_index, result)
                return

            # Truncate string output before publishing to the panel so that
            # large stdout (e.g. ls -la on a home dir) does not flood the UI.
            display_result = (
                truncate_output(result) if isinstance(result, str) else result
            )
            context["last_result"] = display_result
            context["last_action"] = action
            bus.publish(
                "execution_step_success",
                {"task_id": task_id, "step_index": step_index, "result": display_result},
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

        # Skip window focus for:
        # 1. Plugin actions — background/automation plugins don't need a focused window
        # 2. Shell/command actions — they run in terminals, not in specific GUI windows
        #    (run_command, execute, git_op etc. target the terminal, not the active app)
        # 3. Any action where no specific window target is needed
        # Focusing causes failures when the active window title doesn't resolve
        # via wmctrl/xdotool (e.g. "Untitled - Google Chrome" is generic).
        _SKIP_FOCUS_FOR_METHODS = {"plugin"}
        _needs_focus = (
            preferred_method not in _SKIP_FOCUS_FOR_METHODS
            and action not in _SHELL_ACTIONS   # shell actions target terminal, not GUI window
        )
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
            # BUG 1 FIX: panel_sudo commands must SKIP terminal_resolver entirely.
            # The executor was resolving Ghost/Bridge BEFORE shell_tool got to check
            # needs_password, then injecting _profile=GhostTarget which overrode
            # the interactive flow. panel_sudo has its own subprocess strategy
            # (sudo -S + ProcessBridge) that is incompatible with the profile system.
            #
            # BUG 2 FIX: task_id was never injected into step args, so shell_tool
            # and process_bridge received task_id="unknown" for every command.
            # Inject it here, before profile resolution or dispatch.
            args = dict(args)
            args["task_id"] = task_id   # BUG 2 FIX — always stamp task_id into args

            is_panel_sudo = (
                args.get("needs_password")
                or args.get("profile_hint") == "panel_sudo"
            )

            if not is_panel_sudo:
                # Normal path — resolve execution profile via terminal_resolver
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
                    args["task_id"]  = task_id   # keep task_id on rebuilt args too
                    args["_profile"] = chosen_profile
                    args["cwd"]      = context.get("cwd")
            else:
                # panel_sudo path — skip terminal_resolver, just stamp cwd
                # shell_tool._execute_interactive handles everything from here
                args["cwd"] = args.get("cwd") or context.get("cwd")

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

                    # ── Arg normalization ──────────────────────────────────
                    # The intent-parser produces generic shapes (e.g.
                    # {'command': 'open', 'args': ['cursor']}) that may not
                    # match the plugin's declared parameter names (e.g.
                    # 'app_name').  Normalize before validating so the plugin
                    # receives the right keys regardless of how the planner
                    # phrased the step.
                    plugin_args = self._normalize_args_for_plugin(
                        plugin_instance, args or {}, action, context,
                    )

                    validation_error = plugin_instance.validate(plugin_args)
                    if validation_error:
                        # Normalization couldn't fully satisfy the plugin —
                        # log and fall through to the waterfall rather than
                        # dispatching with known-bad args.
                        logger.warning(
                            "Plugin '%s' validation still failed after normalization: %s "
                            "— skipping plugin, falling through to waterfall.",
                            plugin_name, validation_error,
                        )
                    else:
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
                                plugin_instance.run(context, plugin_args),
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

        # DOUBLE-EXECUTION FIX:
        # capability_registry.execute() runs the actual shell/tool command
        # internally (via command_ops / file_ops etc.).  It returns
        # (True, action_data_dict) on success or (False, error_str) on failure.
        #
        # The old waterfall treated (True, dict) as "capability returned a
        # descriptor — now find a tool and run it again", causing shell_tool
        # to execute the same command twice:
        #   1st run → capability_registry.execute() → shell_tool internally
        #   2nd run → waterfall tier → tool_selector → shell_tool again
        #
        # FIX: call capability_registry.execute() ONCE before the waterfall.
        # • If it succeeds  → return immediately, skip the waterfall entirely.
        # • If it fails with a real error (not "no capability registered") →
        #   return immediately as a definitive failure for this action so the
        #   waterfall does NOT re-run the same command via a different tier.
        # • If no capability is registered (unknown intent) → fall through to
        #   the waterfall so tool_selector / ollama_tool can handle it.
        _NO_CAP_PREFIX = "No capability registered"
        try:
            cap_ok, cap_result = await capability_registry.execute(action, context, args)

            if cap_ok:
                # Capability ran and succeeded.
                if isinstance(cap_result, dict):
                    if "success" in cap_result:
                        if cap_result["success"]:
                            return True, cap_result.get("result"), "plugin"
                        else:
                            # Capability ran but reported failure — definitive.
                            last_error = cap_result.get("result", "Capability failed")
                            return False, last_error, "plugin"
                    else:
                        # Plain dict result (e.g. file_ops returns metadata)
                        return True, cap_result, "plugin"
                else:
                    return True, cap_result, "plugin"

            else:
                # cap_ok is False.
                cap_err_str = str(cap_result)
                if _NO_CAP_PREFIX not in cap_err_str:
                    # A real capability was found but it failed (e.g. command
                    # exited non-zero, file not found, network error).
                    # Do NOT re-run via the waterfall — that would execute the
                    # same shell command a second time.
                    logger.warning(
                        "Capability '%s' failed: %s — not retrying via waterfall.",
                        action, cap_result,
                    )
                    last_error = cap_result
                    category = await self._classify_error_dynamically(str(last_error))
                    # Still honour retry logic (e.g. transient network errors)
                    # but re-run through capability_registry, not a second tool.
                    should_retry = await retry_manager.should_retry(
                        task_id, step_index, error_type=category
                    )
                    if not should_retry:
                        return (
                            False,
                            {"type": "exhausted", "message": last_error, "tried": [action]},
                            "plugin",
                        )
                # else: "No capability registered" → fall through to waterfall below

        except Exception as cap_exc:
            logger.debug(
                "capability_registry.execute pre-check raised for '%s': %s — "
                "falling through to waterfall.",
                action, cap_exc,
            )

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
                # Waterfall tier: use tool_selector's chosen tool directly.
                # capability_registry was already called above and either
                # succeeded (returned early) or found no registered capability.
                ok, tool_result = await tool_instance.run(action, args)
                if ok:
                    return True, tool_result, method_used
                last_error = tool_result

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
    def _normalize_args_for_plugin(
        plugin_instance: Any,
        args: dict,
        action: str,
        context: dict,
    ) -> dict:
        """
        Translate the executor's raw step-args into the shape a plugin expects.

        The intent-parser produces generic shapes like
            {'command': 'open', 'args': ['cursor']}
        while a plugin may declare specific parameters like 'app_name'.
        This method asks the plugin to validate the raw args first; if that
        fails it runs a series of heuristic mappings derived from the
        validation error message and the plugin's own parameter names,
        then re-validates.  The original args are never mutated.

        Mapping rules (applied in order, first match wins per missing key):
          1. If error mentions a key name and that key appears in one of the
             known raw-arg positions, map it directly.
          2. 'app_name'  ← args['args'][0] | args['target'] | args['name']
                           | args['command'] (when command ≠ a known shell verb)
          3. 'file_path' / 'path' ← args['args'][0] | args['file'] | args['target']
          4. 'url'        ← args['args'][0] | args['target']
          5. 'query'      ← args['args'][0] | args['text'] | args['target']
          6. 'text'       ← args['args'][0] | args['query'] | args['target']
          7. Fallback: spread positional args list into the first N missing keys.

        Internal executor keys (_profile, task_id, cwd) are always forwarded
        unchanged so shell_tool and process_bridge keep working.
        """
        _SHELL_VERBS: frozenset[str] = frozenset({
            "open", "run", "execute", "launch", "start",
            "xdg-open", "gtk-launch", "gio",
        })
        _INTERNAL_KEYS: frozenset[str] = frozenset({"_profile", "task_id", "cwd", "profile_hint"})

        # Fast path — no normalization needed
        first_error = plugin_instance.validate(args)
        if first_error is None:
            return args

        normalized = {k: v for k, v in args.items() if k in _INTERNAL_KEYS}
        positional: list[str] = [str(a) for a in (args.get("args") or [])]
        command: str = str(args.get("command") or "").strip().lower()
        target_val: str | None = (
            positional[0] if positional else
            args.get("target") or args.get("name") or None
        )

        # Collect all non-internal, non-positional raw args as candidates
        raw_scalars: dict[str, Any] = {
            k: v for k, v in args.items()
            if k not in _INTERNAL_KEYS and k not in {"args", "command"}
        }

        # Determine which keys the plugin still needs by re-validating an
        # empty candidate dict — we call validate repeatedly below so keep
        # track of what we've already satisfied.
        candidate: dict[str, Any] = dict(raw_scalars)

        # ── Rule 2: app_name ──────────────────────────────────────────────
        if "app_name" not in candidate:
            if target_val and command in _SHELL_VERBS:
                candidate["app_name"] = target_val
            elif command and command not in _SHELL_VERBS:
                candidate["app_name"] = command
            elif target_val:
                candidate["app_name"] = target_val

        # ── Rule 3: file_path / path ──────────────────────────────────────
        for fkey in ("file_path", "path"):
            if fkey not in candidate and target_val:
                candidate[fkey] = target_val

        # ── Rule 4: url ───────────────────────────────────────────────────
        if "url" not in candidate and target_val:
            candidate["url"] = target_val

        # ── Rule 5: query ─────────────────────────────────────────────────
        if "query" not in candidate:
            candidate["query"] = (
                args.get("text") or target_val or action
            )

        # ── Rule 6: text ──────────────────────────────────────────────────
        if "text" not in candidate:
            candidate["text"] = (
                args.get("query") or target_val or action
            )

        # ── Rule 7: generic positional spread ─────────────────────────────
        # Re-validate to find remaining missing keys, then fill from positional
        test_args = {**normalized, **candidate}
        remaining_error = plugin_instance.validate(test_args)
        if remaining_error and positional:
            # Parse "Missing 'key'" style messages
            import re as _re
            missing_keys = _re.findall(r"['\"](\w+)['\"]", remaining_error)
            for i, mkey in enumerate(missing_keys):
                if mkey not in candidate and i < len(positional):
                    candidate[mkey] = positional[i]

        normalized.update(candidate)

        # Final validation log
        final_error = plugin_instance.validate(normalized)
        if final_error:
            logger.debug(
                "_normalize_args_for_plugin: still invalid after normalization: %s "
                "(action=%s, raw_args=%s)",
                final_error, action, args,
            )
        else:
            logger.debug(
                "_normalize_args_for_plugin: normalized args for action='%s': %s",
                action, {k: v for k, v in normalized.items() if k not in _INTERNAL_KEYS},
            )

        return normalized

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