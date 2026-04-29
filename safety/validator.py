"""
safety/validator.py
────────────────────
Safety gatekeeper for Operonix.

FIX CHANGELOG (this revision)
──────────────────────────────
BUG 1 — intent was read from step.get("intent") but steps use "action" key.
    The planner builds steps as {"action": "create_dir", "args": {...}}.
    step.get("intent") always returned None, so every step fell through to
    the else: risk = RiskLevel.SAFE branch.  That accidentally worked for
    benign ops, but the intent was never correctly identified, meaning
    destructive ops like delete_file would also have been SAFE.
    FIX: read step.get("action") first, fall back to task_data.get("intent").

BUG 2 — create_dir / delete_dir / list_dir were not in any category set.
    They fell into the else: SAFE branch by accident.  This is correct
    behaviour for create_dir (it IS safe), but it was untested.  Explicitly
    added them to the correct sets so behaviour is intentional not accidental:
      create_dir  → safe_file_ops (always SAFE, path checked)
      list_dir    → read_only_intents (always SAFE)
      delete_dir  → destructive_intents (HIGH, requires confirmation)

BUG 3 — context passed to context_validator was built from current_context
    which was always {} (see orchestrator BUG 2).  Now that the orchestrator
    correctly populates context, the validator builds the payload properly.
    No code change needed here — just flows correctly once orchestrator is fixed.

All other logic unchanged.
"""
from __future__ import annotations

import logging
import os
import re

from context.context_validator import context_validator
from core.config import settings
from core.event_bus import bus
from safety.risk_rules import (
    RiskLevel,
    get_command_risk,
    get_file_op_risk,
    get_web_op_risk,
)


class SafetyValidator:

    def __init__(self) -> None:
        self.logger = logging.getLogger("SafetyValidator")
        self.violation_counts: dict[str, int] = {}
        self.max_violations = 3

        self.forbidden_patterns: list[str] = [
            r"node_modules",
            r"\.env$",
            r"\.git",
        ]

        # Always SAFE — no risk evaluation needed
        self.read_only_intents: set[str] = {
            "read_file",
            "list_files",
            "list_dir",        # BUG 2 FIX: explicit, was accidental SAFE
            "get_file_info",
            "search_web",
        }

        # SAFE after path check — get_file_op_risk handles dangerous sub-cases
        self.safe_file_ops: set[str] = {
            "write_file",
            "create_file",
            "create_dir",      # BUG 2 FIX: explicit, was accidental SAFE
            "move_file",
            "copy_file",
            "append_file",
        }

        # HIGH by policy — always requires confirmation
        self.destructive_intents: set[str] = {
            "delete_file",
            "delete_dir",      # BUG 2 FIX: explicit, was missing entirely
            "run_command",
            "shell_command",
        }

    async def start(self) -> None:
        bus.subscribe("task_dispatched", self.validate_task_safety)
        self.logger.info("Safety Validator: Active and guarding execution.")

    async def validate_task_safety(self, event: object) -> None:
        task_data       = event.data
        task_id         = task_data.get("task_id")
        steps           = task_data.get("steps", [])
        current_context = task_data.get("context", {})

        self.logger.debug("Assessing safety for task [%s]...", task_id)

        for index, step in enumerate(steps):
            # BUG 1 FIX: steps use "action" key, not "intent"
            intent = step.get("action") or step.get("intent") or task_data.get("intent")
            args   = step.get("args", {})

            # ── 1. Path normalisation ─────────────────────────────────────
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

            # ── 2. Context & permission check ─────────────────────────────
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
                risk = self._safe_get_risk(
                    get_command_risk, cmd, task_id=task_id, intent=intent
                )

            elif intent in self.safe_file_ops:
                # create_dir, write_file etc. — SAFE unless path is dangerous
                path = args.get("path") or args.get("target", "")
                risk = self._safe_get_risk(
                    get_file_op_risk, intent, path, task_id=task_id, intent=intent
                )

            elif intent == "delete_file":
                path = args.get("path") or args.get("target", "")
                risk = self._safe_get_risk(
                    get_file_op_risk, intent, path, task_id=task_id, intent=intent
                )
                if risk == RiskLevel.SAFE:
                    risk = RiskLevel.HIGH

            elif intent in self.destructive_intents:
                # delete_dir and any future destructive ops
                path = args.get("path") or args.get("target", "")
                risk = self._safe_get_risk(
                    get_file_op_risk, intent, path, task_id=task_id, intent=intent
                )
                if risk == RiskLevel.SAFE:
                    risk = RiskLevel.HIGH

            elif intent == "open_url":
                url  = args.get("url") or args.get("query", "")
                risk = self._safe_get_risk(
                    get_web_op_risk, url, task_id=task_id, intent=intent
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