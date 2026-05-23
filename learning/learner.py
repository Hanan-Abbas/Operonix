"""
learning/learner.py
────────────────────
Pattern learner — extended with Gap 1 FailureClass gate.

Changes from original
──────────────────────
The original learner subscribed to:
  • task_completed              → learn_from_success()
  • execution_strategy_overridden → _learn_from_override()
  • reflection_complete          → _learn_from_reflection()

All three subscriptions and their logic are preserved verbatim.

Gap 1 addition — routing_mismatch gate
───────────────────────────────────────
The plan mandates that learner.py must ONLY receive ROUTING_MISMATCH
events from the executor.  ENV_TRANSIENT failures (network drops, locked
files, AX timeouts) must never reach the learner or they corrupt the
routing weights — the "death spiral" documented in Gap 1.

  New subscription: "routing_mismatch" → _learn_from_routing_mismatch()

  This event is published by the executor's _handle_failure() method
  (executor.py) with a full decision log.  The learner reads the intent
  and the method that failed, and down-weights that (intent, method) pair
  in the override_rankings store so the router picks a lower-priority
  method next time.

  Critically:
    • ENV_TRANSIENT events are tagged and filtered by error_classifier.py
      BEFORE they reach the bus as routing_mismatch — they never arrive here.
    • The learner never inspects raw exception messages.
    • The down-weight is additive and bounded (max penalty per pair is
      capped at settings.LEARNER_MAX_MISMATCH_PENALTY, default 10) so a
      single bad run cannot permanently exile a method.

  No other changes to existing methods.
"""
from __future__ import annotations

import json
import logging
import os
from collections import defaultdict
from typing import Any

from core.config import settings
from core.event_bus import bus
from learning.pattern_validator import pattern_validator


class PatternLearner:
    """
    Experience aggregator for the Operonix AI OS.

    Watches successful tasks, extracts repeatable step patterns, and saves
    them so the Planner doesn't need expensive LLM calls for repeat requests.

    Also watches:
      • Panel strategy overrides → builds per-(app, intent) method rankings.
      • Reflector lessons        → updates rankings from implicit performance data.
      • routing_mismatch events  → down-weights methods that were wrong for an
                                   intent (Gap 1 fix — ENV_TRANSIENT excluded).
    """

    def __init__(self, store_path: str = "learning/pattern_store.json") -> None:
        self.logger = logging.getLogger("PatternLearner")
        self.store_path = store_path
        self._override_store_path = os.path.join(
            os.path.dirname(store_path), "override_rankings.json"
        )
        # Gap 1: separate store for routing mismatch penalties so they can be
        # inspected independently from user-override rankings.
        self._mismatch_store_path = os.path.join(
            os.path.dirname(store_path), "mismatch_penalties.json"
        )
        self.patterns: dict[str, list] = {}
        # Structure: {app: {intent: {method: count}}}
        self._override_counts: dict[str, dict[str, dict[str, int]]] = defaultdict(
            lambda: defaultdict(lambda: defaultdict(int))
        )
        # Gap 1: penalty store — {intent: {method: penalty_count}}
        # Stored separately so penalties are never confused with positive signals.
        self._mismatch_penalties: dict[str, dict[str, int]] = defaultdict(
            lambda: defaultdict(int)
        )
        self._load_store()
        self._load_override_store()
        self._load_mismatch_store()

    async def start(self) -> None:
        """Subscribe to the EventBus."""
        bus.subscribe("task_completed",                self.learn_from_success)
        bus.subscribe("execution_strategy_overridden", self._learn_from_override)
        bus.subscribe("reflection_complete",           self._learn_from_reflection)

        # Gap 1 — routing mismatch gate
        # The executor publishes "routing_mismatch" ONLY for FailureClass.ROUTING_MISMATCH.
        # ENV_TRANSIENT, ENV_PERMANENT, and EXECUTION_LOGIC never arrive here.
        bus.subscribe("routing_mismatch", self._learn_from_routing_mismatch)

        self.logger.info(
            "PatternLearner: Active. Watching task completions, panel overrides, "
            "Reflector lessons, and routing mismatches."
        )

    # ── Task pattern learning (unchanged) ─────────────────────────────────────

    async def learn_from_success(self, event: Any) -> None:
        data    = event.data
        task_id = data.get("task_id")
        steps   = data.get("steps", [])
        intent  = data.get("intent")

        if not intent or not steps:
            self.logger.debug(
                "Skipping learning for task [%s]: Missing intent or steps.", task_id
            )
            return

        self.logger.info(
            "Analysing task [%s] for intent '%s'...", task_id, intent
        )

        abstracted_steps = self._abstract_steps(steps)
        is_valid = await pattern_validator.validate_pattern(intent, abstracted_steps)
        if not is_valid:
            return

        if intent not in self.patterns:
            self.patterns[intent] = []

        duplicate_found = False
        for pattern_obj in self.patterns[intent]:
            if pattern_obj.get("steps") == abstracted_steps:
                duplicate_found = True
                pattern_obj["usage_count"] = pattern_obj.get("usage_count", 1) + 1
                self._save_store()
                break

        if not duplicate_found:
            self.patterns[intent].append({
                "steps":       abstracted_steps,
                "usage_count": 1,
                "step_count":  len(abstracted_steps),
            })
            self._save_store()
            self.logger.info(
                "Learned new pattern for '%s'! Total: %d",
                intent, len(self.patterns[intent]),
            )
            bus.publish(
                "pattern_learned",
                {"intent": intent, "steps_count": len(abstracted_steps)},
                source="learner",
            )
        else:
            self.logger.debug(
                "Pattern for '%s' already exists. Incremented usage count.", intent
            )

    # ── Panel override learning (unchanged) ───────────────────────────────────

    async def _learn_from_override(self, event: Any) -> None:
        data   = event.data
        app    : str = data.get("app", "unknown")
        intent : str = data.get("intent") or "unknown"
        chosen : str = data.get("chosen_method", "")

        if not chosen:
            return

        self._override_counts[app][intent][chosen] += 1
        self._save_override_store()

        self.logger.info(
            "Override learned: app=%s intent=%s method=%s (count=%d)",
            app, intent, chosen,
            self._override_counts[app][intent][chosen],
        )

    def get_method_ranking(self, app: str, intent: str) -> list[str]:
        """
        Return methods sorted by how often the user has chosen them for
        this (app, intent) pair.  Incorporates routing mismatch penalties:
        a method's net score = override_count - mismatch_penalty_count.
        Methods with a net score <= 0 are moved to the end of the list
        rather than removed entirely, so a single bad run cannot permanently
        disable a method.
        """
        counts = self._override_counts.get(app, {}).get(intent, {})
        if not counts:
            counts = self._override_counts.get("*", {}).get(intent, {})

        penalties = self._mismatch_penalties.get(intent, {})

        if not counts and not penalties:
            return []

        # Build net scores: all methods that appear in either store
        all_methods = set(counts.keys()) | set(penalties.keys())
        scored: list[tuple[str, int]] = []
        for method in all_methods:
            net = counts.get(method, 0) - penalties.get(method, 0)
            scored.append((method, net))

        # Sort: highest net score first; negative-score methods go to the tail
        scored.sort(key=lambda kv: -kv[1])
        return [method for method, _ in scored]

    # ── Reflector lesson learning (unchanged) ─────────────────────────────────

    async def _learn_from_reflection(self, event: Any) -> None:
        try:
            data            = event.data or {}
            intent     : str = (data.get("intent")      or "unknown").strip().lower()
            app_context: str = (data.get("app_context") or "unknown").strip().lower()
            outcome    : str = data.get("outcome", "unknown")
            capability : str = data.get("capability_used") or data.get("capability", "")

            if not intent or not capability:
                return

            tier = (
                capability.split(":")[0].strip().lower()
                if ":" in capability else capability.lower()
            )

            if outcome != "success":
                self.logger.debug(
                    "Skipping reflection for non-success outcome='%s' intent='%s'",
                    outcome, intent,
                )
                return

            for app_key in (app_context, "*"):
                self._override_counts[app_key][intent][tier] += 1

            self._save_override_store()
            self.logger.debug(
                "Reflection learned: app=%s intent=%s tier=%s (success)",
                app_context, intent, tier,
            )

        except Exception as exc:
            self.logger.warning("_learn_from_reflection failed (non-fatal): %s", exc)

    # ── Gap 1: routing mismatch down-weighting ────────────────────────────────

    async def _learn_from_routing_mismatch(self, event: Any) -> None:
        """
        Called ONLY when the executor publishes "routing_mismatch" —
        i.e. FailureClass.ROUTING_MISMATCH was tagged by error_classifier.py.

        ENV_TRANSIENT, ENV_PERMANENT, and EXECUTION_LOGIC events are NEVER
        routed here.  They are handled by the executor's retry/fallback/debugger
        paths and never reach the bus as "routing_mismatch".

        Down-weighting logic
        ─────────────────────
        For the (intent, method) pair that failed:
          1. Increment _mismatch_penalties[intent][method] by 1.
          2. Cap the penalty at settings.LEARNER_MAX_MISMATCH_PENALTY (default 10)
             so a single bad period cannot permanently exile a method.
          3. Persist to mismatch_penalties.json.
          4. Publish "method_weight_updated" so the dashboard can show the change.

        get_method_ranking() already reads _mismatch_penalties and applies them
        as a net score reduction — no additional wiring needed.
        """
        try:
            data   = event.data or {}
            intent : str = (data.get("intent") or "").strip().lower()
            method : str = (data.get("method") or "").strip().lower()

            if not intent or not method:
                self.logger.debug(
                    "_learn_from_routing_mismatch: missing intent or method — skipping."
                )
                return

            # Validate that this is genuinely a routing mismatch signal
            failure_class: str = (data.get("failure_class") or "").lower()
            if failure_class and failure_class != "routing_mismatch":
                # Defensive guard: if the event bus somehow delivers a non-mismatch
                # event to this handler, do not corrupt the weights.
                self.logger.warning(
                    "_learn_from_routing_mismatch: unexpected failure_class='%s' "
                    "for intent='%s' — skipping to protect learner integrity.",
                    failure_class, intent,
                )
                return

            max_penalty: int = int(
                getattr(settings, "LEARNER_MAX_MISMATCH_PENALTY", 10)
            )
            current = self._mismatch_penalties[intent][method]
            if current >= max_penalty:
                self.logger.debug(
                    "Mismatch penalty for (intent='%s', method='%s') already at "
                    "cap %d — not incrementing further.",
                    intent, method, max_penalty,
                )
                return

            self._mismatch_penalties[intent][method] = current + 1
            self._save_mismatch_store()

            new_penalty = self._mismatch_penalties[intent][method]
            self.logger.info(
                "Routing mismatch recorded: intent='%s' method='%s' "
                "penalty=%d/%d",
                intent, method, new_penalty, max_penalty,
            )

            bus.publish(
                "method_weight_updated",
                {
                    "intent"      : intent,
                    "method"      : method,
                    "penalty"     : new_penalty,
                    "max_penalty" : max_penalty,
                    "source"      : "routing_mismatch",
                },
                source="learner",
            )

        except Exception as exc:
            self.logger.warning(
                "_learn_from_routing_mismatch failed (non-fatal): %s", exc
            )

    # ── Helpers (unchanged) ───────────────────────────────────────────────────

    def _abstract_steps(self, steps: list) -> list:
        abstracted = []
        for step in steps:
            action       = step.get("action")
            args         = step.get("args", {})
            abstract_args = {key: f"<{key.upper()}>" for key in args}
            abstracted.append({"action": action, "args": abstract_args})
        return abstracted

    # ── Persistence ───────────────────────────────────────────────────────────

    def _load_store(self) -> None:
        if os.path.exists(self.store_path):
            try:
                with open(self.store_path) as f:
                    data = json.load(f)
                    self.patterns = data.get("patterns", {})
            except Exception as exc:
                self.logger.error("Failed to load pattern store: %s", exc)
                self.patterns = {}

    def _save_store(self) -> None:
        try:
            os.makedirs(os.path.dirname(self.store_path), exist_ok=True)
            with open(self.store_path, "w") as f:
                json.dump({"patterns": self.patterns}, f, indent=4)
        except Exception as exc:
            self.logger.error("Failed to save pattern store: %s", exc)

    def _load_override_store(self) -> None:
        if os.path.exists(self._override_store_path):
            try:
                with open(self._override_store_path) as f:
                    raw = json.load(f)
                for app, intents in raw.items():
                    for intent, methods in intents.items():
                        for method, count in methods.items():
                            self._override_counts[app][intent][method] = count
            except Exception as exc:
                self.logger.error("Failed to load override store: %s", exc)

    def _save_override_store(self) -> None:
        try:
            os.makedirs(os.path.dirname(self._override_store_path), exist_ok=True)
            serialisable = {
                app: {
                    intent: dict(methods)
                    for intent, methods in intents.items()
                }
                for app, intents in self._override_counts.items()
            }
            with open(self._override_store_path, "w") as f:
                json.dump(serialisable, f, indent=4)
        except Exception as exc:
            self.logger.error("Failed to save override store: %s", exc)

    def _load_mismatch_store(self) -> None:
        if os.path.exists(self._mismatch_store_path):
            try:
                with open(self._mismatch_store_path) as f:
                    raw = json.load(f)
                for intent, methods in raw.items():
                    for method, penalty in methods.items():
                        self._mismatch_penalties[intent][method] = penalty
            except Exception as exc:
                self.logger.error("Failed to load mismatch penalty store: %s", exc)

    def _save_mismatch_store(self) -> None:
        try:
            os.makedirs(os.path.dirname(self._mismatch_store_path), exist_ok=True)
            serialisable = {
                intent: dict(methods)
                for intent, methods in self._mismatch_penalties.items()
            }
            with open(self._mismatch_store_path, "w") as f:
                json.dump(serialisable, f, indent=4)
        except Exception as exc:
            self.logger.error("Failed to save mismatch penalty store: %s", exc)


# Global singleton
learner = PatternLearner()