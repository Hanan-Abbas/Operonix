"""
learning/retriever.py

Panel integration
─────────────────
`get_method_ranking(app, intent)` is a new synchronous method called by the
panel's SuggestionEngine to re-order execution strategies based on what the
user has historically preferred for a given (app, intent) pair.

The method delegates to `learner.get_method_ranking()` which owns the live
in-memory override counter.  The retriever is kept as a thin pass-through so
the panel never has to import the learner directly.
"""
from __future__ import annotations

import json
import logging
import os
from typing import Any


class Retriever:
    """🔎 The memory recall unit of the AI OS.

    Fetches saved task patterns from the store and hydrates them with the
    current task's specific arguments.

    Also exposes `get_method_ranking` for the panel suggestion engine.
    """

    def __init__(self, store_path: str = "learning/pattern_store.json") -> None:
        self.logger = logging.getLogger("PatternRetriever")
        self.store_path = store_path

    # ── Panel-facing method ───────────────────────────────────────────────────

    def get_method_ranking(self, app: str, intent: str) -> list[str]:
        """
        Return an ordered list of execution methods (e.g. ["command", "plugin"])
        based on historical user overrides for this (app, intent) pair.

        Returns [] when no data exists — the suggestion engine falls back to
        the default waterfall order in that case.

        Delegates to the global `learner` instance (which holds the live
        override counter) so we never read from disk on every keystroke.
        """
        try:
            from learning.learner import learner  # lazy to avoid circular import
            return learner.get_method_ranking(app=app, intent=intent)
        except Exception as exc:  # noqa: BLE001
            self.logger.debug("get_method_ranking: learner unavailable — %s", exc)
            return []

    # ── Existing pattern retrieval (unchanged) ────────────────────────────────

    async def get_pattern_for_intent(
        self, intent: str, current_args: dict
    ) -> list | None:
        """Checks if a pattern exists for the given intent and returns a
        hydrated plan.
        """
        patterns = self._load_patterns()

        if intent not in patterns or not patterns[intent]:
            self.logger.debug("No saved patterns found for intent: '%s'", intent)
            return None

        self.logger.info("🎯 Found %d saved pattern(s) for '%s'", len(patterns[intent]), intent)

        # Pick the pattern with the highest usage_count.
        chosen_pattern = max(
            patterns[intent],
            key=lambda p: p.get("usage_count", 1) if isinstance(p, dict) else 1,
        )
        # Support both legacy list format and new object format.
        steps = chosen_pattern.get("steps", chosen_pattern) if isinstance(chosen_pattern, dict) else chosen_pattern
        return self._hydrate_steps(steps, current_args)

    def _hydrate_steps(self, abstract_steps: list, real_args: dict) -> list:
        """Replaces generic placeholders like <PATH> with real arguments."""
        hydrated_steps = []
        for step in abstract_steps:
            action = step.get("action")
            abstract_args = step.get("args", {})
            realized_args = {}
            for key, placeholder in abstract_args.items():
                lookup_key = key.lower()
                if lookup_key in real_args:
                    realized_args[key] = real_args[lookup_key]
                else:
                    realized_args[key] = placeholder
                    self.logger.warning("Missing argument '%s' for pattern placeholder.", lookup_key)
            hydrated_steps.append({"action": action, "args": realized_args})
        return hydrated_steps

    def _load_patterns(self) -> dict:
        if os.path.exists(self.store_path):
            try:
                with open(self.store_path) as f:
                    data = json.load(f)
                    return data.get("patterns", {})
            except Exception as exc:
                self.logger.error("Failed to load pattern store: %s", exc)
        return {}


# Global instance
retriever = Retriever()