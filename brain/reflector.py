"""
brain/reflector.py
──────────────────
Post-execution reflection engine for Operonix.

Role in the system
──────────────────
The Reflector closes the cognitive loop. After every task cycle the
Orchestrator calls `reflect()` with the full ExecutionResult. The
Reflector then:

  1. Scores the outcome (success / partial / failure + root-cause)
  2. Extracts structured lessons (what worked, what broke, why)
  3. Calibrates per-capability confidence scores stored in long-term memory
  4. Writes episodic memories so future planning starts smarter
  5. Detects recurring failure patterns and emits self-evolution events
     so the PluginEvolver / CapabilityGapDetector / Learner can act

Event flow
──────────
  Orchestrator
      └─► Executor ──► [ACTION TAKEN]
                            └─► Reflector.reflect()
                                    ├─► memory.episodic.store()
                                    ├─► memory.long_term.update_confidence()
                                    ├─► event_bus.publish("reflection_complete", lesson)
                                    └─► event_bus.publish("evolution_needed", gap)  ← conditional
"""

from __future__ import annotations

import json
import logging
import time
from dataclasses import dataclass, field, asdict
from enum import Enum
from typing import Any

from core.event_bus import EventBus
from core.config import Settings
from memory.episodic import EpisodicMemory
from memory.long_term_memory import LongTermMemory
from brain.llm_client import LLMClient

logger = logging.getLogger(__name__)


# ─────────────────────────────────────────────
# Domain types
# ─────────────────────────────────────────────

class OutcomeGrade(str, Enum):
    SUCCESS   = "success"
    PARTIAL   = "partial"
    FAILURE   = "failure"
    UNKNOWN   = "unknown"


class FailureCategory(str, Enum):
    CAPABILITY_MISSING  = "capability_missing"   # no plugin / api / command / ui path worked
    PERMISSION_DENIED   = "permission_denied"     # safety / sandbox blocked it
    CONTEXT_MISMATCH    = "context_mismatch"      # wrong app state when action ran
    AMBIGUOUS_INTENT    = "ambiguous_intent"      # planner chose wrong capability
    TRANSIENT_ERROR     = "transient_error"       # timeout, crash — likely recoverable
    UNKNOWN             = "unknown"


@dataclass
class Lesson:
    """Structured insight produced by one reflection cycle."""
    timestamp:          float
    intent:             str
    capability_used:    str                   # e.g. "plugin:coding_plugin", "api", "command", "ui_fallback"
    app_context:        str                   # active application at execution time
    outcome:            OutcomeGrade
    failure_category:   FailureCategory | None
    root_cause:         str                   # human-readable explanation
    suggested_fix:      str                   # actionable recommendation
    confidence_delta:   float                 # how much to shift capability trust score
    evolution_needed:   bool                  # should PluginEvolver / gap detector be triggered?
    raw_result:         dict[str, Any]        # original ExecutionResult payload (for audit)

    def to_dict(self) -> dict[str, Any]:
        d = asdict(self)
        d["outcome"]           = self.outcome.value
        d["failure_category"]  = self.failure_category.value if self.failure_category else None
        return d


@dataclass
class ReflectionStats:
    """Running counters — kept in-process; periodically flushed to long-term memory."""
    total_reflections:  int = 0
    successes:          int = 0
    partial:            int = 0
    failures:           int = 0
    evolution_triggers: int = 0
    # capability → (successes, attempts)
    capability_hits:    dict[str, list[int]] = field(default_factory=dict)

    def hit_rate(self, capability: str) -> float:
        hits, attempts = self.capability_hits.get(capability, [0, 0])
        return hits / attempts if attempts else 0.0


# ─────────────────────────────────────────────
# Reflector
# ─────────────────────────────────────────────

class Reflector:
    """
    Analyses every ExecutionResult and feeds structured lessons back into
    memory, confidence scores, and the event bus.

    Parameters
    ----------
    event_bus       : shared EventBus instance
    episodic        : EpisodicMemory for short-to-medium term storage
    long_term       : LongTermMemory for persistent confidence / pattern data
    llm_client      : LLMClient used for deeper root-cause analysis
    settings        : global Settings object
    """

    # How many consecutive failures of the same capability before we flag
    # evolution_needed = True.
    _EVOLUTION_FAILURE_THRESHOLD: int = 3

    # Confidence adjustment magnitudes
    _CONFIDENCE_SUCCESS_BUMP:  float =  0.05
    _CONFIDENCE_FAILURE_DROP:  float = -0.10
    _CONFIDENCE_PARTIAL_DROP:  float = -0.03

    def __init__(
        self,
        event_bus:  EventBus,
        episodic:   EpisodicMemory,
        long_term:  LongTermMemory,
        llm_client: LLMClient,
        settings:   Settings,
    ) -> None:
        self._bus       = event_bus
        self._episodic  = episodic
        self._long_term = long_term
        self._llm       = llm_client
        self._settings  = settings
        self._stats     = ReflectionStats()

        # capability → consecutive failure count (resets on any success)
        self._consecutive_failures: dict[str, int] = {}

        # Subscribe to execution results published by the Orchestrator/Executor
        self._bus.subscribe("execution_complete", self._on_execution_complete)
        logger.info("[Reflector] initialised — listening for execution_complete events")

    # ─────────────────────────────────────────
    # Public API
    # ─────────────────────────────────────────

    async def reflect(self, execution_result: dict[str, Any]) -> Lesson:
        """
        Main entry point. Call this (or let the event subscription call it)
        after every task execution.

        Parameters
        ----------
        execution_result : dict produced by executor.Executor, expected keys:
            intent          str   – user intent label
            capability      str   – which capability tier handled it
            app_context     str   – active app name at runtime
            success         bool
            partial         bool  – true when action ran but outcome was incomplete
            error           str | None
            error_type      str | None
            steps           list[dict]  – individual step results
            duration_ms     float
        """
        try:
            lesson = await self._analyse(execution_result)
            await self._store_lesson(lesson)
            await self._update_confidence(lesson)
            self._check_evolution_trigger(lesson)
            self._update_stats(lesson)
            self._bus.publish("reflection_complete", lesson.to_dict())
            logger.info(
                "[Reflector] %s | %s | grade=%s | delta=%.2f",
                lesson.intent,
                lesson.capability_used,
                lesson.outcome.value,
                lesson.confidence_delta,
            )
            return lesson
        except Exception as exc:
            logger.exception("[Reflector] reflection failed: %s", exc)
            # Never let the reflector crash the main loop
            return self._fallback_lesson(execution_result, str(exc))

    def get_capability_confidence(self, capability: str) -> float:
        """
        Returns the current learned trust score for a capability (0.0–1.0).
        Used by the Planner to prefer higher-confidence paths.
        """
        return self._long_term.get_float(
            f"confidence:{capability}", default=0.75
        )

    def get_stats(self) -> dict[str, Any]:
        return asdict(self._stats)

    # ─────────────────────────────────────────
    # Event subscription handler
    # ─────────────────────────────────────────

    async def _on_execution_complete(self, payload: dict[str, Any]) -> None:
        await self.reflect(payload)

    # ─────────────────────────────────────────
    # Core analysis
    # ─────────────────────────────────────────

    async def _analyse(self, result: dict[str, Any]) -> Lesson:
        """
        Determines outcome grade, failure category, root cause, and fix.
        Uses LLM for deeper analysis only when the outcome is non-trivially bad.
        """
        intent       = result.get("intent", "unknown")
        capability   = result.get("capability", "unknown")
        app_context  = result.get("app_context", "unknown")
        success      = result.get("success", False)
        partial      = result.get("partial", False)
        error        = result.get("error")
        error_type   = result.get("error_type")

        # ── Grade ────────────────────────────────────────────────────────────
        if success and not partial:
            outcome = OutcomeGrade.SUCCESS
        elif partial or (success and error):
            outcome = OutcomeGrade.PARTIAL
        elif not success:
            outcome = OutcomeGrade.FAILURE
        else:
            outcome = OutcomeGrade.UNKNOWN

        # ── Failure category (rule-based first, fast path) ───────────────────
        failure_category: FailureCategory | None = None
        root_cause = ""
        suggested_fix = ""
        confidence_delta = 0.0

        if outcome == OutcomeGrade.SUCCESS:
            confidence_delta = self._CONFIDENCE_SUCCESS_BUMP
            root_cause    = "Execution completed as expected."
            suggested_fix = "No action needed."

        elif outcome in (OutcomeGrade.PARTIAL, OutcomeGrade.FAILURE):
            failure_category, root_cause, suggested_fix = \
                self._classify_failure(error, error_type, result)

            if outcome == OutcomeGrade.PARTIAL:
                confidence_delta = self._CONFIDENCE_PARTIAL_DROP
            else:
                confidence_delta = self._CONFIDENCE_FAILURE_DROP

            # For complex failures, ask the LLM for deeper insight
            if failure_category in (
                FailureCategory.AMBIGUOUS_INTENT,
                FailureCategory.UNKNOWN,
            ):
                root_cause, suggested_fix = await self._llm_root_cause(
                    intent, capability, app_context, error, result
                )

        evolution_needed = self._should_trigger_evolution(
            capability, outcome, failure_category
        )

        return Lesson(
            timestamp        = time.time(),
            intent           = intent,
            capability_used  = capability,
            app_context      = app_context,
            outcome          = outcome,
            failure_category = failure_category,
            root_cause       = root_cause,
            suggested_fix    = suggested_fix,
            confidence_delta = confidence_delta,
            evolution_needed = evolution_needed,
            raw_result       = result,
        )

    def _classify_failure(
        self,
        error: str | None,
        error_type: str | None,
        result: dict[str, Any],
    ) -> tuple[FailureCategory, str, str]:
        """
        Fast, rule-based failure classifier. Returns (category, root_cause, fix).
        """
        err_lower = (error or "").lower()
        etype     = (error_type or "").lower()

        # Permission / safety blocks
        if any(k in err_lower for k in ("permission denied", "sandbox", "blocked", "not allowed")):
            return (
                FailureCategory.PERMISSION_DENIED,
                f"Action was blocked by the safety or sandbox layer: {error}",
                "Review risk_rules.py or extend sandbox allowed paths if the action is legitimate.",
            )

        # Missing capability
        if any(k in err_lower for k in ("not found", "no plugin", "capability missing", "unsupported")):
            return (
                FailureCategory.CAPABILITY_MISSING,
                f"No plugin, API, command, or UI path could handle this intent: {error}",
                "Trigger CapabilityGapDetector to generate a new plugin for this intent.",
            )

        # Context / app state mismatch
        if any(k in err_lower for k in ("element not found", "window not found", "wrong app", "focus")):
            return (
                FailureCategory.CONTEXT_MISMATCH,
                f"The expected UI state or application was not active: {error}",
                "Add app-state validation in context/context_validator.py before this action.",
            )

        # Transient (timeouts, crashes)
        if any(k in etype for k in ("timeout", "crash", "connection", "ioerror")):
            return (
                FailureCategory.TRANSIENT_ERROR,
                f"A transient system error occurred: {error}",
                "The retry_manager should handle this; check retry limits in executor config.",
            )

        return (
            FailureCategory.UNKNOWN,
            f"Unclassified failure: {error or 'no error message'}",
            "Requires LLM-assisted root cause analysis.",
        )

    def _should_trigger_evolution(
        self,
        capability: str,
        outcome: OutcomeGrade,
        category: FailureCategory | None,
    ) -> bool:
        """
        Returns True if this failure warrants waking up the self-evolution subsystem.
        """
        if outcome == OutcomeGrade.SUCCESS:
            self._consecutive_failures[capability] = 0
            return False

        count = self._consecutive_failures.get(capability, 0) + 1
        self._consecutive_failures[capability] = count

        # Always trigger for missing capabilities
        if category == FailureCategory.CAPABILITY_MISSING:
            return True

        # Trigger after N consecutive failures of any kind
        return count >= self._EVOLUTION_FAILURE_THRESHOLD

    # ─────────────────────────────────────────
    # LLM-assisted root cause
    # ─────────────────────────────────────────

    async def _llm_root_cause(
        self,
        intent: str,
        capability: str,
        app_context: str,
        error: str | None,
        result: dict[str, Any],
    ) -> tuple[str, str]:
        """
        Asks the LLM to diagnose a complex or ambiguous failure.
        Returns (root_cause, suggested_fix).
        """
        steps_summary = json.dumps(result.get("steps", [])[:5], indent=2)
        prompt = f"""
You are the self-reflection module of Operonix, an AI desktop automation system.

A task just failed. Diagnose the root cause and suggest a concrete fix.

# Task Details
- User Intent  : {intent}
- Capability   : {capability}  (plugin > api > command > ui_fallback)
- Active App   : {app_context}
- Error        : {error or "none"}

# Execution Steps (first 5)
{steps_summary}

Respond ONLY as JSON with exactly two keys:
{{
  "root_cause": "<one concise sentence>",
  "suggested_fix": "<one concrete actionable sentence>"
}}
"""
        try:
            response = await self._llm.complete(prompt, temperature=0.2)
            data = json.loads(response.strip().lstrip("```json").rstrip("```"))
            return data.get("root_cause", "LLM analysis unavailable"), \
                   data.get("suggested_fix", "No suggestion available")
        except Exception as exc:
            logger.warning("[Reflector] LLM root-cause call failed: %s", exc)
            return f"LLM analysis failed: {exc}", "Manual inspection required."

    # ─────────────────────────────────────────
    # Memory & confidence persistence
    # ─────────────────────────────────────────

    async def _store_lesson(self, lesson: Lesson) -> None:
        """Write the lesson into episodic memory for future retrieval by the Planner."""
        try:
            await self._episodic.store(
                key     = f"lesson:{lesson.intent}:{int(lesson.timestamp)}",
                content = lesson.to_dict(),
                tags    = [lesson.intent, lesson.capability_used, lesson.outcome.value],
            )
        except Exception as exc:
            logger.warning("[Reflector] episodic store failed: %s", exc)

    async def _update_confidence(self, lesson: Lesson) -> None:
        """
        Adjusts the persisted confidence score for the capability that ran.
        Score is clamped to [0.05, 1.0] so nothing is ever fully abandoned.
        """
        key     = f"confidence:{lesson.capability_used}"
        current = self._long_term.get_float(key, default=0.75)
        updated = max(0.05, min(1.0, current + lesson.confidence_delta))
        try:
            self._long_term.set_float(key, updated)
            logger.debug(
                "[Reflector] confidence %s: %.3f → %.3f",
                lesson.capability_used, current, updated,
            )
        except Exception as exc:
            logger.warning("[Reflector] confidence update failed: %s", exc)

    # ─────────────────────────────────────────
    # Evolution trigger
    # ─────────────────────────────────────────

    def _check_evolution_trigger(self, lesson: Lesson) -> None:
        """
        If the lesson flags evolution_needed, publish an event so the
        PluginEvolver, CapabilityGapDetector, or Learner can respond.
        """
        if not lesson.evolution_needed:
            return

        payload = {
            "intent":           lesson.intent,
            "capability":       lesson.capability_used,
            "app_context":      lesson.app_context,
            "failure_category": lesson.failure_category.value if lesson.failure_category else None,
            "root_cause":       lesson.root_cause,
            "suggested_fix":    lesson.suggested_fix,
        }
        self._bus.publish("evolution_needed", payload)
        self._stats.evolution_triggers += 1
        logger.warning(
            "[Reflector] ⚡ evolution_needed published for capability '%s' (intent: %s)",
            lesson.capability_used,
            lesson.intent,
        )

    # ─────────────────────────────────────────
    # Stats
    # ─────────────────────────────────────────

    def _update_stats(self, lesson: Lesson) -> None:
        self._stats.total_reflections += 1
        cap = lesson.capability_used

        if cap not in self._stats.capability_hits:
            self._stats.capability_hits[cap] = [0, 0]

        self._stats.capability_hits[cap][1] += 1   # attempts

        if lesson.outcome == OutcomeGrade.SUCCESS:
            self._stats.successes += 1
            self._stats.capability_hits[cap][0] += 1
        elif lesson.outcome == OutcomeGrade.PARTIAL:
            self._stats.partial += 1
        else:
            self._stats.failures += 1

    # ─────────────────────────────────────────
    # Fallback (never crash the loop)
    # ─────────────────────────────────────────

    @staticmethod
    def _fallback_lesson(result: dict[str, Any], error: str) -> Lesson:
        return Lesson(
            timestamp        = time.time(),
            intent           = result.get("intent", "unknown"),
            capability_used  = result.get("capability", "unknown"),
            app_context      = result.get("app_context", "unknown"),
            outcome          = OutcomeGrade.UNKNOWN,
            failure_category = FailureCategory.UNKNOWN,
            root_cause       = f"Reflection itself failed: {error}",
            suggested_fix    = "Check reflector logs.",
            confidence_delta = 0.0,
            evolution_needed = False,
            raw_result       = result,
        )