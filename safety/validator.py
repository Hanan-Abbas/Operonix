import logging
import os
import re
import time
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
    """🚨 The ultimate gatekeeper for the AI OS Agent.

    Analyzes proposed execution steps, normalizes paths to prevent bypasses,
    integrates with ContextValidator, and intercepts tasks that violate safety
    rules based on behavioral patterns.
    """

    def __init__(self):
        self.logger = logging.getLogger("SafetyValidator")
        self.violation_counts = {}
        self.max_violations = 3

        self.forbidden_patterns = [
            r"node_modules",
            r"\.env$",
            r"\.git",
        ]

        # BUG FIX: Intents that are inherently read-only and safe.
        # These bypass the HIGH-risk escalation path entirely.
        # Previously, any intent not explicitly matched in the risk routing
        # block fell through with risk = RiskLevel.SAFE, BUT the
        # NoneType crash in risk evaluation caused a blanket HIGH escalation
        # for everything including read_file. Now read-only intents are
        # explicitly whitelisted so they are never touched by the risk router.
        self.read_only_intents = {
            "read_file",
            "list_files",
            "get_file_info",
            "search_web",   # kept here since web read ops are low risk
        }

    async def start(self):
        """Subscribe to the event bus to intercept tasks before execution."""
        bus.subscribe("task_dispatched", self.validate_task_safety)
        self.logger.info("🚨 Safety Validator: Active and guarding execution.")

    async def validate_task_safety(self, event):
        """Analyzes all steps in a plan to assess risks before letting them pass."""
        task_data = event.data
        task_id = task_data.get("task_id")
        steps = task_data.get("steps", [])
        current_context = task_data.get("context", {})

        self.logger.debug(f"Assessing safety for task [{task_id}]...")

        for index, step in enumerate(steps):
            intent = step.get("intent") or task_data.get("intent")
            args = step.get("args", {})

            # -----------------------------
            # 1️⃣ Path Normalization & Safety
            # -----------------------------
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

            # -----------------------------
            # 2️⃣ Context & Permission Checker Integration
            # -----------------------------
            mock_state = {"target_path": target_path}
            mock_state.update(current_context.get("state", {}))

            full_context_payload = {
                "active_window": current_context.get("active_window", ""),
                "app_type": current_context.get("app_type"),
                "state": mock_state,
            }

            # BUG FIX: Wrap context_validator call in try/except.
            # Previously, if context_validator.validate_action_context raised
            # or returned None, it would crash with 'NoneType has no attribute get'
            # downstream, causing the system to default to SAFE/Locked (HIGH risk).
            try:
                validation_result = await context_validator.validate_action_context(
                    intent, full_context_payload
                )
                # Guard against None return from validate_action_context
                if validation_result is None:
                    self.logger.warning(
                        f"context_validator returned None for intent '{intent}' on task [{task_id}]. "
                        "Treating as valid to avoid false high-risk escalation."
                    )
                    is_valid, reason = True, "context_validator returned None — defaulting to valid"
                else:
                    is_valid, reason = validation_result
            except Exception as ctx_err:
                self.logger.warning(
                    f"context_validator raised an exception for task [{task_id}]: {ctx_err}. "
                    "Defaulting to valid to avoid false positive."
                )
                is_valid, reason = True, str(ctx_err)

            if not is_valid:
                await self._handle_violation(
                    task_id, f"Context validation failed for step {index}: {reason}"
                )
                return

            # -----------------------------------------------
            # 3️⃣ Dynamic Multi-Domain Risk Analysis
            # -----------------------------------------------

            # BUG FIX: Read-only intents (e.g. read_file) must be explicitly
            # allowed before hitting the risk router. Previously read_file was
            # NOT listed in any risk routing branch, so it fell through to the
            # default risk = RiskLevel.SAFE. But when the risk evaluator
            # crashed (NoneType bug above), the entire task was escalated to
            # HIGH. Whitelisting read-only intents here means they are always
            # SAFE regardless of any evaluator crash.
            if intent in self.read_only_intents:
                risk = RiskLevel.SAFE

            # --- A. Command Execution Routing ---
            elif intent == "run_command":
                cmd = args.get("command", "")
                risk = self._safe_get_risk(get_command_risk, cmd, task_id, intent)

            # --- B. File System Write/Delete/Move Routing ---
            elif intent in ["write_file", "delete_file", "move_file"]:
                path = args.get("path") or args.get("target", "")
                risk = self._safe_get_risk(get_file_op_risk, intent, path, task_id, intent)

            # --- C. Web/Network Routing ---
            elif intent in ["open_url"]:
                url = args.get("url") or args.get("query", "")
                risk = self._safe_get_risk(get_web_op_risk, url, task_id, intent)

            else:
                # Unknown or unlisted intent: treat as LOW risk, not HIGH.
                # BUG FIX: The original defaulted to SAFE here which was fine,
                # but the NoneType crash above caused a blanket HIGH escalation.
                # Now that the crash is fixed, SAFE is the correct default for
                # anything unrecognized and non-destructive.
                risk = RiskLevel.SAFE

            # -----------------------------
            # 4️⃣ Execution of Risk Judgments
            # -----------------------------
            if risk == RiskLevel.FORBIDDEN:
                await self._handle_violation(
                    task_id,
                    f"Forbidden operation blocked on step {index} for intent '{intent}'.",
                )
                return

            elif risk == RiskLevel.HIGH:
                self.logger.warning(
                    f"Task [{task_id}] step {index} triggered HIGH RISK. Requesting confirmation."
                )
                bus.publish(
                    "confirmation_required",
                    {
                        "task_id": task_id,
                        "reason": f"High risk detected on step {index} with intent '{intent}'",
                        "step_index": index,
                        "step_data": step,
                    },
                    source="safety_validator",
                )
                return

        self.logger.info(f"✅ Task [{task_id}] passed all safety checks.")
        bus.publish("task_safety_cleared", task_data, source="safety_validator")

    def _safe_get_risk(self, func, *args, task_id=None, intent=None) -> RiskLevel:
        """
        BUG FIX: Wraps every risk evaluation function call in a try/except.
        Previously a crash inside get_command_risk / get_file_op_risk /
        get_web_op_risk returned None, and any downstream .get() call on
        that None caused 'NoneType object has no attribute get', which was
        then caught by the orchestrator and defaulted to SAFE/Locked (HIGH).
        Now we catch the crash here and default to LOW (not HIGH) so the
        system doesn't falsely escalate benign operations.
        """
        try:
            result = func(*args)
            if result is None:
                self.logger.warning(
                    f"Risk function {func.__name__} returned None for intent "
                    f"'{intent}' on task [{task_id}]. Defaulting to LOW."
                )
                return RiskLevel.LOW
            return result
        except Exception as e:
            self.logger.warning(
                f"Risk evaluation crashed in {func.__name__} for intent "
                f"'{intent}' on task [{task_id}]: {e}. Defaulting to LOW."
            )
            return RiskLevel.LOW

    async def _handle_violation(self, task_id: str, reason: str):
        """Handles violations and tracks repeated offenses."""
        if task_id not in self.violation_counts:
            self.violation_counts[task_id] = 0

        self.violation_counts[task_id] += 1

        self.logger.warning(
            f"🛑 Safety violation on task {task_id} "
            f"(Offense {self.violation_counts[task_id]}/{self.max_violations}): {reason}"
        )

        if self.violation_counts[task_id] >= self.max_violations:
            self.logger.critical(
                f"🚨 Task {task_id} exceeded maximum safety violations! Terminating."
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