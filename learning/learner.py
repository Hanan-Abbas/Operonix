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

    def _abstract_steps(self, steps):
        """Replaces specific arguments with generic placeholders.

        Turns: {'action': 'write_file', 'args': {'path': 'C:/user/notes.txt'}}
        Into: {'action': 'write_file', 'args': {'path': '<PATH>'}}
        """
        abstracted = []
        for step in steps:
            action = step.get("action")
            args = step.get("args", {})

            # We keep the keys but clear the specific values to make it a template
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
            # Ensure directory exists
            os.makedirs(os.path.dirname(self.store_path), exist_ok=True)
            with open(self.store_path, "w") as f:
                json.dump({"patterns": self.patterns}, f, indent=4)
        except Exception as e:
            self.logger.error(f"Failed to save pattern store: {e}")


# Global instance
learner = PatternLearner()