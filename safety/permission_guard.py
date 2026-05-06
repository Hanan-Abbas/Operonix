"""
safety/permission_guard.py — Operonix AI OS Agent
══════════════════════════════════════════════════
Pre-execution permission gate.

Sits between the orchestrator and the executor in the event chain:

  orchestrator  →  task_dispatched
                       │
                       ▼
               PermissionGuard.check()
                  ├─ SAFE / LOW  →  task_safety_cleared   (executor runs)
                  ├─ HIGH        →  confirmation_required  (human decides)
                  └─ FORBIDDEN   →  task_failed            (hard block)

The SafetyValidator (safety/validator.py) performs deep per-step analysis
using path normalisation, context validation, and pattern matching.
PermissionGuard is intentionally lightweight — it is the FIRST gate and
checks only the top-level intent and command string so that obviously safe
tasks (read_file, search_web) are cleared instantly without spinning up the
full validator machinery.

Event flow:
  • Subscribes to  : "task_dispatched"
  • Publishes to   : "task_safety_cleared"   (SAFE / LOW)
                     "confirmation_required"  (HIGH)
                     "task_failed"            (FORBIDDEN)
"""
from __future__ import annotations

import logging
from typing import Any

from core.event_bus import bus
from safety.risk_rules import (
    RiskLevel,
    get_command_risk,
    get_file_op_risk,
    get_web_op_risk,
)

logger = logging.getLogger("PermissionGuard")

# ---------------------------------------------------------------------------
# Intent → risk-function routing table
# ---------------------------------------------------------------------------

# Intents that are always SAFE — cleared immediately, no analysis needed.
_ALWAYS_SAFE: frozenset[str] = frozenset({
    "read_file", "list_files", "list_dir", "get_file_info",
    "search_web", "open_url",
})

# Intents whose risk depends on the command string.
_COMMAND_INTENTS: frozenset[str] = frozenset({
    "run_command", "shell_command",
})

# Intents whose risk depends on the file path.
_FILE_INTENTS: frozenset[str] = frozenset({
    "write_file", "create_file", "create_dir", "move_file",
    "copy_file", "append_file", "delete_file", "delete_dir",
})

# Intents that are HIGH risk by policy regardless of args.
_HIGH_BY_POLICY: frozenset[str] = frozenset({
    "delete_file", "delete_dir",
})


class PermissionGuard:
    """
    Lightweight first-pass permission gate subscribed to "task_dispatched".

    Performs intent-level risk assessment using safety.risk_rules and
    immediately routes to task_safety_cleared, confirmation_required, or
    task_failed without blocking the event loop.
    """

    def __init__(self) -> None:
        self._started = False

    # ── Lifecycle ──────────────────────────────────────────────────────────

    async def start(self) -> None:
        if self._started:
            return
        bus.subscribe("task_dispatched", self.check)
        self._started = True
        logger.info("PermissionGuard: online — guarding task_dispatched.")

    # ── Main gate ──────────────────────────────────────────────────────────

    async def check(self, event: Any) -> None:
        """
        Entry point for every dispatched task.

        Reads intent + parameters from the event, evaluates risk, and routes
        to the appropriate downstream event without modifying the payload.
        """
        data       = event.data if hasattr(event, "data") else event
        task_id    = data.get("task_id")
        intent     = data.get("intent") or data.get("capability") or ""
        parameters = data.get("parameters") or {}

        # Also check first step if steps list is present (planner output).
        steps = data.get("steps") or []
        if steps and not intent:
            first_step = steps[0]
            intent     = first_step.get("action") or first_step.get("intent") or ""
            parameters = first_step.get("args") or parameters

        logger.debug(
            "PermissionGuard: evaluating task [%s] intent=%r", task_id, intent
        )

        risk = self._evaluate_risk(intent, parameters, task_id)

        if risk == RiskLevel.FORBIDDEN:
            logger.warning(
                "🚫 FORBIDDEN task [%s] intent=%r — hard blocked.", task_id, intent
            )
            bus.publish(
                "task_failed",
                {
                    "task_id": task_id,
                    "error":   f"Forbidden operation: intent '{intent}' is not permitted.",
                    "stage":   "permission_guard",
                },
                source="permission_guard",
            )

        elif risk == RiskLevel.HIGH:
            logger.warning(
                "⚠️ High-risk operation detected: %s. Escalating.", intent
            )
            bus.publish(
                "confirmation_required",
                {
                    "task_id":    task_id,
                    "reason":     f"High-risk intent '{intent}' requires your approval.",
                    "intent":     intent,
                    "parameters": parameters,
                    "risk_level": "high",
                    "step_data":  steps[0] if steps else {},
                    # Full task payload — ConfirmationManager re-publishes this
                    # as task_safety_cleared when the user clicks Allow.
                    "full_task":  data,
                },
                source="permission_guard",
            )

        else:
            # SAFE or LOW — clear immediately.
            logger.info(
                "✅ Task [%s] intent=%r cleared (risk=%s).", task_id, intent, risk.name
            )
            bus.publish(
                "task_safety_cleared",
                data,
                source="permission_guard",
            )

    # ── Risk evaluator ─────────────────────────────────────────────────────

    def _evaluate_risk(
        self,
        intent: str,
        parameters: dict[str, Any],
        task_id: str | None,
    ) -> RiskLevel:
        """
        Map intent + parameters to a RiskLevel using safety.risk_rules.

        Does NOT duplicate SafetyValidator's deep per-step analysis — this
        is intentionally a fast first-pass check on the top-level intent.
        """
        if not intent:
            return RiskLevel.LOW

        # 1. Always-safe intents — skip all analysis.
        if intent in _ALWAYS_SAFE:
            if intent == "open_url":
                url = parameters.get("url") or parameters.get("query", "")
                return self._safe_call(
                    get_web_op_risk, url,
                    task_id=task_id, intent=intent,
                )
            return RiskLevel.SAFE

        # 2. Command-execution intents.
        if intent in _COMMAND_INTENTS:
            cmd  = parameters.get("command", "")
            risk = self._safe_call(
                get_command_risk, cmd,
                task_id=task_id, intent=intent,
            )
            # run_command is always at least HIGH — even a safe-looking command
            # needs explicit user awareness when the AI is running it.
            if risk == RiskLevel.SAFE:
                risk = RiskLevel.HIGH
            return risk

        # 3. File-operation intents.
        if intent in _FILE_INTENTS:
            path = parameters.get("path") or parameters.get("target", "")
            risk = self._safe_call(
                get_file_op_risk, intent, path,
                task_id=task_id, intent=intent,
            )
            if intent in _HIGH_BY_POLICY and risk == RiskLevel.SAFE:
                risk = RiskLevel.HIGH
            return risk

        # 4. Unknown intent — treat as LOW (SafetyValidator will do deeper check).
        logger.debug(
            "PermissionGuard: unknown intent %r for task [%s] — defaulting LOW.",
            intent, task_id,
        )
        return RiskLevel.LOW

    # ── Helper ─────────────────────────────────────────────────────────────

    @staticmethod
    def _safe_call(func, *args, task_id=None, intent=None) -> RiskLevel:
        """Call a risk function, defaulting to LOW on any exception."""
        try:
            result = func(*args)
            return result if result is not None else RiskLevel.LOW
        except Exception as exc:
            logger.warning(
                "PermissionGuard: risk function %s raised for intent=%r task=[%s]: %s — defaulting LOW.",
                getattr(func, '__name__', str(func)), intent, task_id, exc,
            )
            return RiskLevel.LOW


# Global singleton — imported by orchestrator.start()
permission_guard = PermissionGuard()