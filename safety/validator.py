import logging
import os
import re
import time
from context.context_validator import context_validator
from core.config import settings
from core.event_bus import bus

# 🔄 UPDATE: Import all specific risk functions from your new risk_rules file
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

        # Track repeated dangerous attempts to prevent brute-forcing or looping
        self.violation_counts = {}
        self.max_violations = 3

        # Hardcoded fallback regex patterns for critical path blocks
        self.forbidden_patterns = [
            r"node_modules",
            r"\.env$",
            r"\.git",
        ]

    async def start(self):
        """Subscribe to the event bus to intercept tasks before execution."""
        bus.subscribe("task_dispatched", self.validate_task_safety)
        self.logger.info("🚨 Safety Validator: Active and guarding execution.")

    async def validate_task_safety(self, event):
        """Analyzes all steps in a plan to assess risks before letting them

        pass.
        """
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

                # Check against fallback regex patterns
                for pattern in self.forbidden_patterns:
                    if re.search(pattern, normalized_path, re.IGNORECASE):
                        await self._handle_violation(
                            task_id,
                            f"Step {index} attempted to access a restricted pattern: {pattern}",
                        )
                        return

                # Update the args with the normalized path
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

            is_valid, reason = await context_validator.validate_action_context(
                intent, full_context_payload
            )

            if not is_valid:
                await self._handle_violation(
                    task_id, f"Context validation failed for step {index}: {reason}"
                )
                return

            # -----------------------------------------------
            # 3️⃣ Dynamic Multi-Domain Risk Analysis (UPDATED)
            # -----------------------------------------------
            risk = RiskLevel.SAFE

            # --- A. Command Execution Routing ---
            if intent == "run_command":
                cmd = args.get("command", "")
                risk = get_command_risk(cmd)

            # --- B. File System Routing ---
            elif intent in ["write_file", "delete_file", "move_file"]:
                path = args.get("path") or args.get("target", "")
                risk = get_file_op_risk(intent, path)

            # --- C. Web/Network Routing ---
            elif intent in ["open_url", "search_web"]:
                url = args.get("url") or args.get("query", "")
                risk = get_web_op_risk(url)

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
                # Halt execution and request user human-in-the-loop intervention
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

        # If it survives the gauntlet, ship it off to the executor!
        self.logger.info(f"✅ Task [{task_id}] passed all safety checks.")
        bus.publish(
            "task_safety_cleared", task_data, source="safety_validator"
        )

    async def _handle_violation(self, task_id: str, reason: str):
        """Handles violations and tracks repeated offenses."""
        if task_id not in self.violation_counts:
            self.violation_counts[task_id] = 0

        self.violation_counts[task_id] += 1

        self.logger.warning(
            f"🛑 Safety violation on task {task_id} (Offense {self.violation_counts[task_id]}/{self.max_violations}): {reason}"
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