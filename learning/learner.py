import json
import logging
import os
from core.event_bus import bus
from learning.pattern_validator import pattern_validator


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

        # 1. Abstract the steps
        abstracted_steps = self._abstract_steps(steps)

        # 2. Validate
        is_valid = await pattern_validator.validate_pattern(
            intent, abstracted_steps
        )
        if not is_valid:
            return

        # Initialize list if intent is new
        if intent not in self.patterns:
            self.patterns[intent] = []

        # -----------------------------------------------------------------
        # 🔄 NEW: Search through the stored objects to find a duplicate
        # -----------------------------------------------------------------
        duplicate_found = False
        for pattern_obj in self.patterns[intent]:
            if pattern_obj.get("steps") == abstracted_steps:
                duplicate_found = True
                # Optional: Increment how many times this specific strategy worked!
                pattern_obj["usage_count"] = (
                    pattern_obj.get("usage_count", 1) + 1
                )
                self._save_store()
                break

        # -----------------------------------------------------------------
        # 🔄 NEW: If no duplicate exists, save it as a structured object
        # -----------------------------------------------------------------
        if not duplicate_found:
            new_pattern_object = {
                "steps": abstracted_steps,
                "usage_count": 1,
                "step_count": len(abstracted_steps),
            }

            self.patterns[intent].append(new_pattern_object)
            self._save_store()

            self.logger.info(
                f"💾 Learned new pattern for '{intent}'! Total patterns for this intent: {len(self.patterns[intent])}"
            )

            bus.publish(
                "pattern_learned",
                {"intent": intent, "steps_count": len(abstracted_steps)},
                source="learner",
            )
        else:
            self.logger.debug(
                f"Pattern for '{intent}' already exists. Incremented usage count."
            )

    def _abstract_steps(self, steps):
        """Replaces specific arguments with generic placeholders."""
        abstracted = []
        for step in steps:
            action = step.get("action")
            args = step.get("args", {})

            abstract_args = {}
            for key in args.keys():
                abstract_args[key] = f"<{key.upper()}>"

            abstracted.append({"action": action, "args": abstract_args})
        return abstracted

    def _load_store(self):
        """Loads existing patterns from the JSON file."""
        if os.path.exists(self.store_path):
            try:
                with open(self.store_path, "r") as f:
                    data = json.load(f)
                    self.patterns = data.get("patterns", {})
            except Exception as e:
                self.logger.error(f"Failed to load pattern store: {e}")
                self.patterns = {}

    def _save_store(self):
        """Saves patterns back to the JSON file."""
        try:
            os.makedirs(os.path.dirname(self.store_path), exist_ok=True)
            with open(self.store_path, "w") as f:
                json.dump({"patterns": self.patterns}, f, indent=4)
        except Exception as e:
            self.logger.error(f"Failed to save pattern store: {e}")


# Global instance
learner = PatternLearner()