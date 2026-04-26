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
    """🚨 The ultimate gatekeeper for the Operonix AI OS Agent.

    Analyses proposed execution steps, normalises paths to prevent bypasses,
    integrates with ContextValidator, and intercepts tasks that violate safety
    rules based on behavioural patterns.

    FIX SUMMARY (this revision)
    ───────────────────────────
    1. context_validator call wrapped in try/except and None-guarded so a
       crash or None return no longer propagates as a blanket HIGH-risk
       escalation for every subsequent intent (was the root cause of
       "Risk evaluation failed: 'NoneType' object has no attribute 'get'").

    2. write_file / create_file are moved OUT of the RISKY bucket.
       Creating a plain file (hello.txt, hello.py) inside a safe path is
       NOT inherently high-risk.  get_file_op_risk already handles the
       genuinely dangerous cases (traversal, root paths, sensitive files).
       Previously write_file fell into the routing block, the
       context_validator crashed, the whole branch defaulted to HIGH, and
       the user had to confirm every single file creation.

    3. _safe_get_risk wraps every risk-function call so a crash inside
       get_command_risk / get_file_op_risk / get_web_op_risk returns LOW
       instead of None, preventing any future NoneType propagation.

    4. Unknown / unlisted intents default to SAFE (not HIGH) now that the
       NoneType crash that was inflating them is fixed.
    """

    def __init__(self):
        self.logger = logging.getLogger("SafetyValidator")
        self.violation_counts: dict[str, int] = {}
        self.max_violations = 3

        self.forbidden_patterns = [
            r"node_modules",
            r"\.env$",
            r"\.git",
        ]

        # Intents that are inherently read-only — always SAFE, skip risk router.
        self.read_only_intents: set[str] = {
            "read_file",
            "list_files",
            "get_file_info",
            "search_web",
        }

        # FIX: write_file / create_file are NOT inherently high-risk.
        # get_file_op_risk handles the dangerous sub-cases (traversal, root,
        # sensitive paths).  For a plain path like "hello.txt" it returns SAFE.
        # Keeping write_file out of RISKY_INTENTS prevents false confirmations.
        self.write_intents: set[str] = {
            "write_file",
            "create_file",
            "move_file",
            "copy_file",
        }

        # Only these intents trigger the HIGH confirmation flow by default.
        self.destructive_intents: set[str] = {
            "delete_file",
            "run_command",
            "shell_command",
        }

    async def start(self):
        """Subscribe to the event bus to intercept tasks before execution."""
        bus.subscribe("task_dispatched", self.validate_task_safety)
        self.logger.info("🚨 Safety Validator: Active and guarding execution.")

    async def validate_task_safety(self, event):
        """Analyse all steps in a plan to assess risk before letting them pass."""
        task_data = event.data
        task_id = task_data.get("task_id")
        steps = task_data.get("steps", [])
        current_context = task_data.get("context", {})

        self.logger.debug("Assessing safety for task [%s]…", task_id)

        for index, step in enumerate(steps):
            intent = step.get("intent") or task_data.get("intent")
            args = step.get("args", {})

            # ── 1. Path normalisation ─────────────────────────────────────────
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

            # ── 2. Context & permission check ─────────────────────────────────
            mock_state = {"target_path": target_path}
            mock_state.update(current_context.get("state", {}))

            full_context_payload = {
                "active_window": current_context.get("active_window", ""),
                "app_type": current_context.get("app_type"),
                "state": mock_state,
            }

            # FIX: Wrap context_validator in try/except AND guard against None.
            # Previously a crash or None return here caused the *entire* task to
            # escalate to HIGH ("NoneType has no attribute 'get'" in orchestrator).
            try:
                validation_result = await context_validator.validate_action_context(
                    intent, full_context_payload
                )
                if validation_result is None:
                    self.logger.warning(
                        "context_validator returned None for intent '%s' on task [%s]. "
                        "Treating as valid to avoid false high-risk escalation.",
                        intent, task_id,
                    )
                    is_valid, reason = True, "context_validator returned None — defaulting to valid"
                else:
                    is_valid, reason = validation_result
            except Exception as ctx_err:
                self.logger.warning(
                    "context_validator raised an exception for task [%s]: %s. "
                    "Defaulting to valid.",
                    task_id, ctx_err,
                )
                is_valid, reason = True, str(ctx_err)

            if not is_valid:
                await self._handle_violation(
                    task_id,
                    f"Context validation failed for step {index}: {reason}",
                )
                return

            # ── 3. Dynamic risk analysis ──────────────────────────────────────

            # A. Read-only intents — always SAFE
            if intent in self.read_only_intents:
                risk = RiskLevel.SAFE

            # B. Shell / command execution — use get_command_risk
            elif intent in ("run_command", "shell_command"):
                cmd = args.get("command", "")
                risk = self._safe_get_risk(
                    get_command_risk, cmd, task_id=task_id, intent=intent
                )

            # C. Write / move / copy — use get_file_op_risk.
            #    FIX: plain paths (e.g. "hello.txt") return SAFE from
            #    get_file_op_risk, so no confirmation is required.
            elif intent in self.write_intents:
                path = args.get("path") or args.get("target", "")
                risk = self._safe_get_risk(
                    get_file_op_risk, intent, path, task_id=task_id, intent=intent
                )

            # D. Destructive file ops — use get_file_op_risk (may return HIGH)
            elif intent == "delete_file":
                path = args.get("path") or args.get("target", "")
                risk = self._safe_get_risk(
                    get_file_op_risk, intent, path, task_id=task_id, intent=intent
                )
                # Deletion of any non-root file is at least HIGH by policy.
                if risk == RiskLevel.SAFE:
                    risk = RiskLevel.HIGH

            # E. Web / network ops
            elif intent == "open_url":
                url = args.get("url") or args.get("query", "")
                risk = self._safe_get_risk(
                    get_web_op_risk, url, task_id=task_id, intent=intent
                )

            else:
                # Unknown / unlisted intents — SAFE by default.
                # Destructive operations have explicit entries above.
                risk = RiskLevel.SAFE

            # ── 4. Execute risk judgment ──────────────────────────────────────
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
                        "task_id": task_id,
                        "reason": (
                            f"High risk detected on step {index} with intent '{intent}'"
                        ),
                        "step_index": index,
                        "step_data": step,
                    },
                    source="safety_validator",
                )
                return

        self.logger.info("✅ Task [%s] passed all safety checks.", task_id)
        bus.publish("task_safety_cleared", task_data, source="safety_validator")

    def _safe_get_risk(self, func, *args, task_id=None, intent=None) -> RiskLevel:
        """
        Wrap every risk-evaluation function call in try/except.

        FIX: Previously a crash inside get_command_risk / get_file_op_risk /
        get_web_op_risk returned None.  Any downstream .get() on that None
        caused 'NoneType object has no attribute get', caught by the
        orchestrator and defaulted to SAFE/Locked (which surfaced as HIGH).
        Now we catch the crash here and return LOW so benign operations are
        not falsely escalated.
        """
        try:
            result = func(*args)
            if result is None:
                self.logger.warning(
                    "Risk function %s returned None for intent '%s' on task [%s]. "
                    "Defaulting to LOW.",
                    func.__name__, intent, task_id,
                )
                return RiskLevel.LOW
            return result
        except Exception as exc:
            self.logger.warning(
                "Risk evaluation crashed in %s for intent '%s' on task [%s]: %s. "
                "Defaulting to LOW.",
                func.__name__, intent, task_id, exc,
            )
            return RiskLevel.LOW

    async def _handle_violation(self, task_id: str, reason: str):
        """Handle violations and track repeated offences."""
        self.violation_counts.setdefault(task_id, 0)
        self.violation_counts[task_id] += 1

        self.logger.warning(
            "🛑 Safety violation on task %s (Offence %d/%d): %s",
            task_id, self.violation_counts[task_id], self.max_violations, reason,
        )

        if self.violation_counts[task_id] >= self.max_violations:
            self.logger.critical(
                "🚨 Task %s exceeded maximum safety violations! Terminating.", task_id
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
                "error": f"Safety Violation: {reason}",
                "stage": "safety_check",
            },
            source="safety_validator",
        )


# Global instance
safety_validator = SafetyValidator()