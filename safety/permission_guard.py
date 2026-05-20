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

CHANGES FROM PREVIOUS VERSION
──────────────────────────────
BUG 1 — Double confirmation prompt for the same task_id.
    Both the orchestrator AND the planner publish "task_dispatched" for the
    same task, so PermissionGuard.check() fired twice producing two
    confirmation_required events.  The user saw two identical dialogs and
    clicking Allow on the first one left the second one dangling.
    FIX: Added _seen_task_ids dedup set.  The first event for a given task_id
    is processed normally; any subsequent duplicate is silently dropped.
    The set is pruned after 60 s to avoid unbounded growth across long sessions.

BUG 2 — run_command was unconditionally escalated to HIGH even for harmless
    read-only commands like `ls -la`, `pwd`, `cat README.md`.
    Root cause: the old code had `if risk == RiskLevel.SAFE: risk = RiskLevel.HIGH`
    hardcoded for all _COMMAND_INTENTS regardless of what the command actually did.
    FIX: Removed the forced escalation.  get_command_risk() in risk_rules.py now
    contains a proper whitelist of read-only commands (SAFE) and policy-HIGH
    commands that really do need confirmation.  The profile_hint from the LLM /
    terminal_resolver is forwarded into get_command_risk() so the risk function
    can make context-aware decisions (bridge env-modifiers are LOW; unknown ghost
    commands are HIGH).

The SafetyValidator (safety/validator.py) also subscribes to "task_dispatched"
and performs deep per-step analysis.  PermissionGuard is the FIRST, lightweight
gate — it checks only the top-level intent and command string so that obviously
safe tasks are cleared instantly without spinning up the full validator machinery.
"""
from __future__ import annotations

import asyncio
import logging
import time
from typing import Any

from core.event_bus import bus
from safety.risk_rules import (
    RiskLevel,
    get_command_risk,
    get_file_op_risk,
    get_web_op_risk,
)

# ── Service-level risk classification ────────────────────────────────────────
# Maps every VALID_SERVICES token to a RiskLevel.
# This table is the single source of truth for "is this service dangerous?"
# context_builder.build() calls check_services() before provisioning anything.

_SERVICE_RISK: dict[str, RiskLevel] = {
    # read-only / passive — cleared immediately
    "window_context":    RiskLevel.SAFE,
    "app_classifier":    RiskLevel.SAFE,
    "app_profiler":      RiskLevel.SAFE,
    "screen_reader":     RiskLevel.SAFE,
    "session_memory":    RiskLevel.SAFE,
    "episodic_memory":   RiskLevel.SAFE,
    "text_ops":          RiskLevel.SAFE,

    # UI interaction — low, not destructive
    "selector_engine":   RiskLevel.LOW,
    "ui_fallback":       RiskLevel.LOW,
    "ui_tool":           RiskLevel.LOW,
    "ui_ops":            RiskLevel.LOW,

    # file system — LOW for reads, HIGH for writes/deletes handled downstream
    "file_tool":         RiskLevel.LOW,
    "file_ops":          RiskLevel.LOW,
    "smart_file_patcher": RiskLevel.LOW,

    # web / api — LOW, no local side-effects
    "web_ops":           RiskLevel.LOW,
    "api_tool":          RiskLevel.LOW,

    # persistent plugin state — LOW
    "plugin_memory":     RiskLevel.LOW,

    # shell / terminal — HIGH, can execute arbitrary system commands
    "terminal_resolver": RiskLevel.HIGH,
    "shell_tool":        RiskLevel.HIGH,
    "process_bridge":    RiskLevel.HIGH,
}

logger = logging.getLogger("PermissionGuard")

# ---------------------------------------------------------------------------
# Intent → risk-function routing table
# ---------------------------------------------------------------------------

_ALWAYS_SAFE: frozenset[str] = frozenset({
    "read_file", "list_files", "list_dir", "get_file_info",
    "search_web",
})

_COMMAND_INTENTS: frozenset[str] = frozenset({
    "run_command", "shell_command",
})

_FILE_INTENTS: frozenset[str] = frozenset({
    "write_file", "create_file", "create_dir", "move_file",
    "copy_file", "append_file", "delete_file", "delete_dir",
})

_HIGH_BY_POLICY: frozenset[str] = frozenset({
    "delete_file", "delete_dir",
})

# How long to remember a seen task_id to absorb late duplicates (seconds).
_DEDUP_TTL = 60.0


class PermissionGuard:
    """
    Lightweight first-pass permission gate subscribed to "task_dispatched".

    Performs intent-level risk assessment using safety.risk_rules and
    immediately routes to task_safety_cleared, confirmation_required, or
    task_failed without blocking the event loop.
    """

    def __init__(self) -> None:
        self._started = False
        # BUG 1 FIX: dedup map  task_id → timestamp of first seen
        self._seen: dict[str, float] = {}

    # ── Lifecycle ──────────────────────────────────────────────────────────

    async def start(self) -> None:
        if self._started:
            return
        bus.subscribe("task_dispatched", self.check)
        asyncio.create_task(self._prune_seen_loop())
        self._started = True
        logger.info("PermissionGuard: online — guarding task_dispatched.")

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

        # BUG 1 FIX: drop duplicate events for the same task_id
        now = time.monotonic()
        if task_id in self._seen:
            logger.debug(
                "PermissionGuard: duplicate task_dispatched for [%s] — skipping.",
                task_id,
            )
            return
        if task_id:
            self._seen[task_id] = now

        # Also check first step if steps list is present (planner output).
        steps = data.get("steps") or []
        if steps and not intent:
            first_step = steps[0]
            intent     = first_step.get("action") or first_step.get("intent") or ""
            parameters = first_step.get("args") or parameters

        # Extract profile_hint so risk functions can make context-aware decisions.
        # It may live at the top level or inside the first step's args.
        profile_hint: str | None = (
            data.get("profile_hint")
            or (steps[0].get("args", {}).get("profile_hint") if steps else None)
        )

        logger.debug(
            "PermissionGuard: evaluating task [%s] intent=%r profile=%s",
            task_id, intent, profile_hint,
        )

        risk = self._evaluate_risk(intent, parameters, task_id, profile_hint)

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

    # ── Service permission gate ────────────────────────────────────────────
    # Called by context_builder.build() BEFORE any service is instantiated.
    # Returns (allowed: bool, blocked_services: list[str], reason: str)

    def check_services(
        self,
        plugin_name: str,
        allowed_services: list[str],
        task_id: str | None = None,
    ) -> tuple[bool, list[str], str]:
        """
        Gate called by context_builder.build() before provisioning services.

        For each declared service:
          - SAFE / LOW  → provisioned silently
          - HIGH        → publish confirmation_required; block until resolved
          - FORBIDDEN   → hard block, publish task_failed

        Returns:
            (all_clear: bool, blocked: list[str], reason: str)

        `blocked` contains the service tokens that were denied.
        If all_clear is False, context_builder must NOT provision any
        blocked service — it passes only the cleared subset to the plugin.
        """
        blocked: list[str] = []
        high_services: list[str] = []

        for svc in allowed_services:
            risk = _SERVICE_RISK.get(svc, RiskLevel.LOW)

            if risk == RiskLevel.FORBIDDEN:
                reason = (
                    f"Plugin '{plugin_name}' requested forbidden service '{svc}'. "
                    f"This service is permanently blocked."
                )
                logger.warning("🚫 FORBIDDEN service '%s' requested by '%s'.", svc, plugin_name)
                bus.publish(
                    "task_failed",
                    {
                        "task_id": task_id,
                        "error":   reason,
                        "stage":   "permission_guard.check_services",
                    },
                    source="permission_guard",
                )
                blocked.append(svc)

            elif risk == RiskLevel.HIGH:
                high_services.append(svc)

        if high_services:
            reason = (
                f"Plugin '{plugin_name}' requires high-risk services: "
                f"{high_services}. User approval needed."
            )
            logger.warning(
                "⚠️ High-risk services %s requested by plugin '%s' — escalating.",
                high_services, plugin_name,
            )
            bus.publish(
                "confirmation_required",
                {
                    "task_id":          task_id,
                    "reason":           reason,
                    "plugin_name":      plugin_name,
                    "high_services":    high_services,
                    "risk_level":       "high",
                    "source":           "plugin_service_gate",
                },
                source="permission_guard",
            )
            blocked.extend(high_services)

        if blocked:
            return False, blocked, f"Blocked services: {blocked}"

        return True, [], "All services cleared."

    def _evaluate_service_risk(self, svc: str) -> RiskLevel:
        """Return the RiskLevel for a single service token."""
        return _SERVICE_RISK.get(svc, RiskLevel.LOW)

    # ── Risk evaluator ─────────────────────────────────────────────────────

    def _evaluate_risk(
        self,
        intent: str,
        parameters: dict[str, Any],
        task_id: str | None,
        profile_hint: str | None = None,
    ) -> RiskLevel:
        """
        Map intent + parameters to a RiskLevel using safety.risk_rules.

        Forwards profile_hint into get_command_risk() so the risk function
        can distinguish bridge env-modifiers (LOW) from unknown ghost
        commands (HIGH) without hardcoding that logic here.
        """
        if not intent:
            return RiskLevel.LOW

        # 1. Always-safe intents.
        if intent in _ALWAYS_SAFE:
            if intent == "open_url":
                url = parameters.get("url") or parameters.get("query", "")
                return self._safe_call(
                    get_web_op_risk, url,
                    task_id=task_id, intent=intent,
                )
            return RiskLevel.SAFE

        # 2. Command-execution intents.
        #    BUG 2 FIX: removed the unconditional `if risk == SAFE: risk = HIGH`
        #    override.  get_command_risk() now handles whitelist / policy correctly.
        #    profile_hint is forwarded so bridge env-modifiers are not escalated.
        if intent in _COMMAND_INTENTS:
            cmd = parameters.get("command", "")
            return self._safe_call(
                get_command_risk, cmd, profile_hint,
                task_id=task_id, intent=intent,
            )

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

        # 4. Unknown intent — LOW (SafetyValidator does deeper per-step check).
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
                "PermissionGuard: risk function %s raised for intent=%r task=[%s]: %s "
                "— defaulting LOW.",
                getattr(func, "__name__", str(func)), intent, task_id, exc,
            )
            return RiskLevel.LOW


# Global singleton
permission_guard = PermissionGuard()