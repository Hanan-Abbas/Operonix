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

    