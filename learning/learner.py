"""
learning/learner.py

Panel integration
─────────────────
Subscribes to `execution_strategy_overridden` (fired by panel's suggestion
engine whenever the user picks a non-default method).  Stores (app, intent,
chosen_method) tuples in a dedicated override_rankings store so that
`learning/retriever.py` can return a learned method order on the next query.
"""
from __future__ import annotations

import json
import logging
import os
from collections import defaultdict
from typing import Any

from core.event_bus import bus
from learning.pattern_validator import pattern_validator


class PatternLearner:
    """🧠 The experience aggregator of the AI OS.

    Watches successful tasks, extracts repeatable step patterns, and saves
    them so the Planner doesn't have to use expensive LLMs for repeat
    requests.

    Also watches panel strategy overrides and builds a per-(app, intent)
    method ranking that the suggestion engine uses to pre-rank strategies.
    """

    def __init__(self, store_path: str = "learning/pattern_store.json") -> None:
        self.logger = logging.getLogger("PatternLearner")
        self.store_path = store_path
        # Override rankings store path sits alongside pattern_store.json
        self._override_store_path = os.path.join(
            os.path.dirname(store_path), "override_rankings.json"
        )
        self.patterns: dict[str, list] = {}
        # Structure: {app: {intent: {method: count}}}
        self._override_counts: dict[str, dict[str, dict[str, int]]] = defaultdict(
            lambda: defaultdict(lambda: defaultdict(int))
        )
        self._load_store()
        self._load_override_store()

    async def start(self) -> None:
        """Subscribe to the EventBus."""
        bus.subscribe("task_completed",                 self.learn_from_success)
        bus.subscribe("execution_strategy_overridden",  self._learn_from_override)
        self.logger.info(
            "🧠 Pattern Learner: Active. Watching task completions and panel overrides."
        )

    # ── Task pattern learning (unchanged) ─────────────────────────────────────

    async def learn_from_success(self, event: Any) -> None:
        data = event.data
        task_id = data.get("task_id")
        steps = data.get("steps", [])
        intent = data.get("intent")

        if not intent or not steps:
            self.logger.debug("Skipping learning for task [%s]: Missing intent or steps.", task_id)
            return

        self.logger.info("🤔 Analysing task [%s] for intent '%s'...", task_id, intent)

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
                "steps": abstracted_steps,
                "usage_count": 1,
                "step_count": len(abstracted_steps),
            })
            self._save_store()
            self.logger.info(
                "💾 Learned new pattern for '%s'! Total: %d",
                intent, len(self.patterns[intent]),
            )
            bus.publish(
                "pattern_learned",
                {"intent": intent, "steps_count": len(abstracted_steps)},
                source="learner",
            )
        else:
            self.logger.debug("Pattern for '%s' already exists. Incremented usage count.", intent)

    # ── Panel override learning ────────────────────────────────────────────────

    async def _learn_from_override(self, event: Any) -> None:
        """
        Called when the panel fires `execution_strategy_overridden`.
        Increments a per-(app, intent, chosen_method) counter so the
        retriever can derive a ranked method list.

        Payload expected:
            {app, intent, chosen_method, default_method}
        """
        data = event.data
        app: str = data.get("app", "unknown")
        intent: str = data.get("intent") or "unknown"
        chosen: str = data.get("chosen_method", "")

        if not chosen:
            return

        self._override_counts[app][intent][chosen] += 1
        self._save_override_store()

        self.logger.info(
            "📌 Override learned: app=%s intent=%s method=%s (count=%d)",
            app, intent, chosen,
            self._override_counts[app][intent][chosen],
        )

    def get_method_ranking(self, app: str, intent: str) -> list[str]:
        """
        Public synchronous method called by the panel's suggestion engine
        (via `learned_ranking` callback in PanelController).

        Returns methods sorted by how often the user has chosen them for
        this (app, intent) pair, highest first.  Returns [] if no overrides
        recorded yet.
        """
        counts = self._override_counts.get(app, {}).get(intent, {})
        if not counts:
            # Try the wildcard app key so cross-app patterns transfer.
            counts = self._override_counts.get("*", {}).get(intent, {})
        if not counts:
            return []

        ranked = sorted(counts.items(), key=lambda kv: -kv[1])
        return [method for method, _ in ranked]

    # ── Helpers ───────────────────────────────────────────────────────────────

    def _abstract_steps(self, steps: list) -> list:
        abstracted = []
        for step in steps:
            action = step.get("action")
            args = step.get("args", {})
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
                # Rebuild defaultdict structure from plain JSON dict.
                for app, intents in raw.items():
                    for intent, methods in intents.items():
                        for method, count in methods.items():
                            self._override_counts[app][intent][method] = count
            except Exception as exc:
                self.logger.error("Failed to load override store: %s", exc)

    def _save_override_store(self) -> None:
        try:
            os.makedirs(os.path.dirname(self._override_store_path), exist_ok=True)
            # Convert nested defaultdicts to plain dicts for JSON serialisation.
            serialisable = {
                app: {intent: dict(methods) for intent, methods in intents.items()}
                for app, intents in self._override_counts.items()
            }
            with open(self._override_store_path, "w") as f:
                json.dump(serialisable, f, indent=4)
        except Exception as exc:
            self.logger.error("Failed to save override store: %s", exc)


# Global instance
learner = PatternLearner()