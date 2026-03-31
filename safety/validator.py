import logging
import os
import re
import time
from context.context_validator import context_validator
from core.config import settings
from core.event_bus import bus
# We import your rules and your environment checker
from safety.risk_rules import RiskLevel, get_command_risk


class SafetyValidator:
    """🚨 The ultimate gatekeeper for the AI OS Agent.

    Analyzes proposed execution steps, normalizes paths to prevent bypasses,
    integrates with ContextValidator, and intercepts tasks that violate safety
    rules.
    """

    def __init__(self):
        self.logger = logging.getLogger("SafetyValidator")

        # Track repeated dangerous attempts to prevent brute-forcing or looping
        # {task_id: {offense_type: count}}
        self.violation_counts = {}
        self.max_violations = 3

        # Hardcoded critical system paths (Fallback if PermissionChecker is missed)
        self.forbidden_patterns = [
            r"node_modules",
            r"\.env$",
            r"\.git",
        ]

    async def start(self):
        """Subscribe to the event bus to intercept tasks before execution."""
        # Listen for tasks that are ready to run but haven't been executed yet
        # (Fired by the Planner)
        bus.subscribe("task_dispatched", self.validate_task_safety)
        self.logger.info("🚨 Safety Validator: Active and guarding execution.")

    async def validate_task_safety(self, event):
        """Analyzes all steps in a plan to assess risks before letting them

        pass.
        """
        task_data = event.data
        task_id = task_data.get("task_id")
        steps = task_data.get("steps", [])

        # We assume the orchestrator or planner passes the current environmental snapshot
        current_context = task_data.get("context", {})

        self.logger.debug(f"Assessing safety for task [{task_id}]...")

        for index, step in enumerate(steps):
            intent = step.get("intent") or task_data.get(
                "intent"
            )  # fallback to global intent

            # ❌ FIX 1: Map "args" directly since your executor uses "args"
            args = step.get("args", {})

            # -----------------------------
            # 1️⃣ Path Normalization & Safety
            # -----------------------------
            target_path = args.get("path") or args.get("target")
            if target_path:
                # ❌ FIX 4: Normalize paths to prevent "../" bypasses
                normalized_path = os.path.normpath(target_path)

                # Check against regex patterns
                for pattern in self.forbidden_patterns:
                    if re.search(pattern, normalized_path, re.IGNORECASE):
                        await self._handle_violation(
                            task_id,
                            f"Step {index} attempted to access a restricted pattern: {pattern}",
                        )
                        return

                # Update the args with the normalized path so the executor uses the safe version!
                if "path" in args:
                    args["path"] = normalized_path
                elif "target" in args:
                    args["target"] = normalized_path

            # -----------------------------
            # 2️⃣ Context & Permission Checker Integration
            # -----------------------------
            # ❌ FIX 2: We pipe the action through your actual ContextValidator!
            # We construct the state dictionary ContextValidator expects
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

            # -----------------------------
            # 3️⃣ Deep Command Inspection
            # -----------------------------
            # ❌ FIX 3: Check more than just run_command if needed
            if intent == "run_command":
                cmd = args.get("command", "")
                risk = get_command_risk(cmd)

                if risk == RiskLevel.HIGH:
                    # Halt and ask for confirmation!
                    # ❌ FIX 6: Use "publish" instead of "emit" to respect the background queue
                    bus.publish(
                        "confirmation_required",
                        {
                            "task_id": task_id,
                            "reason": f"High risk command detected: {cmd}",
                            "step_index": index,
                        },
                        source="safety_validator",
                    )
                    return
                elif risk == RiskLevel.FORBIDDEN:
                    await self._handle_violation(
                        task_id, f"Forbidden command detected in step {index}: {cmd}"
                    )
                    return

        # If all steps pass the gauntlet, we let the system proceed
        self.logger.info(f"✅ Task [{task_id}] passed all safety checks.")

        # Let the executor know it is safe to proceed!
        bus.publish(
            "task_safety_cleared", task_data, source="safety_validator"
        )

    async def _handle_violation(self, task_id: str, reason: str):
        """❌ FIX 5: Handles violations and tracks repeated offenses."""
        if task_id not in self.violation_counts:
            self.violation_counts[task_id] = 0

        self.violation_counts[task_id] += 1

        self.logger.warning(
            f"🛑 Safety violation on task {task_id} (Offense {self.violation_counts[task_id]}/{self.max_violations}): {reason}"
        )

        # If the task repeatedly triggers violations, lock it out entirely
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

        # Standard failure response
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