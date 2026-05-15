"""
safety/validator.py
────────────────────
Safety gatekeeper for Operonix.

CHANGES FROM PREVIOUS VERSION (this revision)
──────────────────────────────────────────────
BUG 1 (original) — intent was read from step.get("intent") but steps use
    "action" key.  FIX: read step.get("action") first, fall back to
    task_data.get("intent").  [unchanged from previous revision]

BUG 2 (original) — create_dir / delete_dir / list_dir were not in any
    category set.  FIX: explicitly added to correct sets.
    [unchanged from previous revision]

BUG 3 (original) — context passed to context_validator was always {}.
    [unchanged from previous revision — flows correctly once orchestrator fixed]

BUG 4 (this revision) — Double confirmation prompt.
    Both SafetyValidator AND PermissionGuard subscribe to "task_dispatched".
    For a HIGH-risk task each published its own confirmation_required event,
    giving the user two identical dialogs for the same task_id.
    FIX: Added _seen_task_ids dedup set (same pattern as PermissionGuard and
    Executor).  The first task_dispatched event for a given task_id is
    processed normally; any duplicate is silently dropped.

BUG 5 (this revision) — profile_hint not forwarded to get_command_risk().
    The risk function now accepts an optional profile_hint so it can
    distinguish bridge env-modifiers (LOW) from unknown ghost commands (HIGH).
    Without forwarding it, the validator would still classify safe commands
    like `ls` as HIGH if risk_rules later changed its defaults.
    FIX: Extract profile_hint from task_data / step args and pass it through.
"""
from __future__ import annotations

import asyncio
import logging
import os
import re
import time
from typing import Optional

from context.context_validator import context_validator
from core.config import settings
from core.event_bus import bus
from safety.risk_rules import (
    RiskLevel,
    get_command_risk,
    get_file_op_risk,
    get_web_op_risk,
)

_DEDUP_TTL = 60.0  # seconds to remember a processed task_id


class SafetyValidator:

    def __init__(self) -> None:
        self.logger = logging.getLogger("SafetyValidator")
        self.violation_counts: dict[str, int] = {}
        self.max_violations = 3

        # BUG 4 FIX: dedup map  task_id → timestamp of first processing
        self._seen: dict[str, float] = {}

        self.forbidden_patterns: list[str] = [
            r"node_modules",
            r"\.env$",
            r"\.git",
        ]

        self.read_only_intents: set[str] = {
            "read_file",
            "list_files",
            "list_dir",
            "get_file_info",
            "search_web",
        }

        self.safe_file_ops: set[str] = {
            "write_file",
            "create_file",
            "create_dir",
            "move_file",
            "copy_file",
            "append_file",
        }

        self.destructive_intents: set[str] = {
            "delete_file",
            "delete_dir",
            "run_command",
            "shell_command",
        }

    async def start(self) -> None:
        bus.subscribe("task_dispatched", self.validate_task_safety)
        asyncio.create_task(self._prune_seen_loop())
        self.logger.info("Safety Validator: Active and guarding execution.")

    # ── Dedup helpers ──────────────────────────────────────────────────────

    async def _prune_seen_loop(self) -> None:
        """Background loop: remove expired task_id entries every 30 s."""
        while True:
            await asyncio.sleep(30)
            now     = time.monotonic()
            expired = [tid for tid, ts in self._seen.items()
                       if now - ts > _DEDUP_TTL]
            for tid in expired:
                self._seen.pop(tid, None)

    # ── Main validation entry ──────────────────────────────────────────────

    async def validate_task_safety(self, event: object) -> None:
        task_data       = event.data
        task_id         = task_data.get("task_id")
        steps           = task_data.get("steps", [])
        current_context = task_data.get("context", {})

        # BUG 4 FIX: drop duplicate events for the same task_id
        now = time.monotonic()
        if task_id in self._seen:
            self.logger.debug(
                "SafetyValidator: duplicate task_dispatched for [%s] — skipping.",
                task_id,
            )
            return
        if task_id:
            self._seen[task_id] = now

        self.logger.debug("Assessing safety for task [%s]...", task_id)

        # BUG 5 FIX: extract profile_hint for forwarding to get_command_risk()
        profile_hint: Optional[str] = task_data.get("profile_hint")
        if not profile_hint and steps:
            profile_hint = steps[0].get("args", {}).get("profile_hint")

        for index, step in enumerate(steps):
            # BUG 1 FIX: steps use "action" key, not "intent"
            intent = step.get("action") or step.get("intent") or task_data.get("intent")
            args   = step.get("args", {})

            # ── 1. Path normalisation ──────────────────────────────────────
            target_path = args.get("path") or args.get("target")
            if target_path:
                normalized_path = os.path.normpath(target_path)
                for pattern in self.forbidden_patterns:
                    if re.search(pattern, normalized_path, re.IGNORECASE):
                        await self._handle_violation(
                            task_id,
                            f"Step {index} attempted to access a restricted pattern: {pattern}",
                        )
                        return
                if "path" in args:
                    args["path"] = normalized_path
                elif "target" in args:
                    args["target"] = normalized_path

            # ── 2. Context & permission check ──────────────────────────────
            mock_state = {"target_path": target_path}
            mock_state.update(current_context.get("state", {}))

            full_context_payload = {
                "active_window": current_context.get("active_window", ""),
                "app_type":      current_context.get("app_type"),
                "state":         mock_state,
            }

            try:
                validation_result = await context_validator.validate_action_context(
                    intent, full_context_payload
                )
                if validation_result is None:
                    self.logger.warning(
                        "context_validator returned None for intent '%s' on task [%s]. "
                        "Treating as valid.",
                        intent, task_id,
                    )
                    is_valid, reason = True, "context_validator returned None"
                else:
                    is_valid, reason = validation_result
            except Exception as ctx_err:
                self.logger.warning(
                    "context_validator raised for task [%s]: %s. Defaulting to valid.",
                    task_id, ctx_err,
                )
                is_valid, reason = True, str(ctx_err)

            if not is_valid:
                await self._handle_violation(
                    task_id,
                    f"Context validation failed for step {index}: {reason}",
                )
                return

            # ── 3. Risk analysis ───────────────────────────────────────────

            if intent in self.read_only_intents:
                risk = RiskLevel.SAFE

            elif intent in ("run_command", "shell_command"):
                cmd  = args.get("command", "")
                # BUG 5 FIX: pass profile_hint so run_command on safe cmds
                # (ls, pwd, cat) resolves SAFE instead of always HIGH.
                risk = self._safe_get_risk(
                    get_command_risk, cmd, profile_hint,
                    task_id=task_id, intent=intent,
                )

            elif intent in self.safe_file_ops:
                path = args.get("path") or args.get("target", "")
                risk = self._safe_get_risk(
                    get_file_op_risk, intent, path,
                    task_id=task_id, intent=intent,
                )

            elif intent == "delete_file":
                path = args.get("path") or args.get("target", "")
                risk = self._safe_get_risk(
                    get_file_op_risk, intent, path,
                    task_id=task_id, intent=intent,
                )
                if risk == RiskLevel.SAFE:
                    risk = RiskLevel.HIGH

            elif intent in self.destructive_intents:
                path = args.get("path") or args.get("target", "")
                risk = self._safe_get_risk(
                    get_file_op_risk, intent, path,
                    task_id=task_id, intent=intent,
                )
                if risk == RiskLevel.SAFE:
                    risk = RiskLevel.HIGH

            elif intent == "open_url":
                url  = args.get("url") or args.get("query", "")
                risk = self._safe_get_risk(
                    get_web_op_risk, url,
                    task_id=task_id, intent=intent,
                )

            else:
                risk = RiskLevel.SAFE

            # ── 4. Risk judgment ───────────────────────────────────────────
            if risk == RiskLevel.FORBIDDEN:
                await self._handle_violation(
                    task_id,
                    f"Forbidden operation blocked on step {index} for intent '{intent}'.",
                )
                return

            elif risk == RiskLevel.HIGH:
                self.logger.warning(
                    "⚠️ High-risk operation detected: %s. Escalating.", intent,
                )
                self.logger.warning(
                    "Task [%s] step %d triggered HIGH RISK. Requesting confirmation.",
                    task_id, index,
                )
                bus.publish(
                    "confirmation_required",
                    {
                        "task_id":    task_id,
                        "reason":     f"High risk on step {index} intent '{intent}'",
                        "step_index": index,
                        "step_data":  step,
                        "intent":     intent,
                        "parameters": args,
                        "risk_level": "high",
                        "full_task":  task_data,
                    },
                    source="safety_validator",
                )
                return

        self.logger.info("Task [%s] passed all safety checks.", task_id)
        bus.publish("task_safety_cleared", task_data, source="safety_validator")

    def _safe_get_risk(self, func, *args, task_id=None, intent=None) -> RiskLevel:
        try:
            result = func(*args)
            if result is None:
                self.logger.warning(
                    "Risk function %s returned None for intent '%s' task [%s]. "
                    "Defaulting to LOW.",
                    func.__name__, intent, task_id,
                )
                return RiskLevel.LOW
            return result
        except Exception as exc:
            self.logger.warning(
                "Risk evaluation crashed in %s for intent '%s' task [%s]: %s. "
                "Defaulting to LOW.",
                func.__name__, intent, task_id, exc,
            )
            return RiskLevel.LOW

    async def _handle_violation(self, task_id: str, reason: str) -> None:
        self.violation_counts.setdefault(task_id, 0)
        self.violation_counts[task_id] += 1

        self.logger.warning(
            "Safety violation on task %s (Offence %d/%d): %s",
            task_id,
            self.violation_counts[task_id],
            self.max_violations,
            reason,
        )

        if self.violation_counts[task_id] >= self.max_violations:
            self.logger.critical(
                "Task %s exceeded maximum safety violations! Terminating.", task_id
            )
            bus.publish(
                "task_aborted",
                {"task_id": task_id, "reason": "Max safety violations reached"},
                source="safety_validator",
            )
            return

        bus.publish(
            "task_failed",
            {
                "task_id": task_id,
                "error":   f"Safety Violation: {reason}",
                "stage":   "safety_check",
            },
            source="safety_validator",
        )


safety_validator = SafetyValidator()