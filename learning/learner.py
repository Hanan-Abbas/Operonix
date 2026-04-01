import json
import logging
import os
from core.event_bus import bus


class PatternLearner:
    """🧠 The experience aggregator of the AI OS.

    Watches successful tasks, extracts repeatable step patterns, and saves them
    so the Planner doesn't have to use expensive LLMs for repeat requests.
    """

    def __init__(self, store_path="learning/pattern_store.json"):
        self.logger = logging.getLogger("PatternLearner")
        self.store_path = store_path
        self.patterns = {}
        self._load_store()

    async def start(self):
        """Subscribe to the event bus to listen for successful executions."""
        # We listen for fully completed tasks to ensure we only learn winning strategies!
        bus.subscribe("task_completed", self.learn_from_success)
        self.logger.info(
            "🧠 Pattern Learner: Active. Watching for successful tasks to memorize."
        )

    async def learn_from_success(self, event):
        """Analyzes a completed task and saves the pattern if it's worth

        remembering.
        """
        data = event.data
        task_id = data.get("task_id")

        # In a real run, the tracker or executor would pass the full resolved plan here
        # For now, we assume the successful steps are passed in the event payload
        steps = data.get("steps", [])
        intent = data.get("intent")

        if not intent or not steps:
            self.logger.debug(
                f"Skipping learning for task [{task_id}]: Missing intent or steps."
            )
            return

        self.logger.info(
            f"🤔 Analyzing successful task [{task_id}] for intent '{intent}'..."
        )

        # Abstract the steps (remove specific hardcoded user parameters like specific file names)
        # to make the pattern reusable for other files!
        abstracted_steps = self._abstract_steps(steps)

        # Save the pattern
        if intent not in self.patterns:
            self.patterns[intent] = []

        # Check if we already have this exact sequence of actions saved
        if abstracted_steps not in self.patterns[intent]:
            self.patterns[intent].append(abstracted_steps)
            self._save_store()
            self.logger.info(
                f"💾 Learned new pattern for '{intent}'! Total patterns for this intent: {len(self.patterns[intent])}"
            )

            # Inform the system that the AI has evolved!
            bus.publish(
                "pattern_learned",
                {"intent": intent, "steps_count": len(abstracted_steps)},
                source="learner",
            )
        else:
            self.logger.debug(
                f"Pattern for '{intent}' already exists. Skipping duplicate."
            )

    