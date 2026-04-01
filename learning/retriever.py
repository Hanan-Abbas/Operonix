import json
import logging
import os


class PatternRetriever:
    """🔎 The memory recall unit of the AI OS.

    Fetches saved task patterns from the store and hydrates them with the
    current task's specific arguments.
    """

    def __init__(self, store_path="learning/pattern_store.json"):
        self.logger = logging.getLogger("PatternRetriever")
        self.store_path = store_path

    async def get_pattern_for_intent(
        self, intent: str, current_args: dict
    ) -> list or None:
        """Checks if a pattern exists for the given intent and returns a

        hydrated plan.
        """
        patterns = self._load_patterns()

        # 1. Direct lookup: Do we have a saved pattern for this exact intent?
        if intent not in patterns or not patterns[intent]:
            self.logger.debug(f"No saved patterns found for intent: '{intent}'")
            return None

        self.logger.info(
            f"🎯 Found {len(patterns[intent])} saved pattern(s) for '{intent}'"
        )

        # 2. Grab the first successful pattern (for now, we just take the first one)
        # In the future, you can score or rank them here!
        chosen_pattern = patterns[intent][0]

        # 3. Hydrate the pattern (Swap <PLACEHOLDERS> with real user arguments)
        hydrated_steps = self._hydrate_steps(chosen_pattern, current_args)

        return hydrated_steps

    def _hydrate_steps(self, abstract_steps: list, real_args: dict) -> list:
        """Replaces generic placeholders like <PATH> with real arguments from

        the user.
        """
        hydrated_steps = []

        for step in abstract_steps:
            action = step.get("action")
            abstract_args = step.get("args", {})
            realized_args = {}

            for key, placeholder in abstract_args.items():
                # If the placeholder is <PATH>, we look for 'path' in the user's arguments
                lookup_key = key.lower()

                if lookup_key in real_args:
                    realized_args[key] = real_args[lookup_key]
                else:
                    # Fallback if the user omitted a required argument
                    realized_args[key] = placeholder
                    self.logger.warning(
                        f"Missing argument '{lookup_key}' to fill pattern placeholder!"
                    )

            hydrated_steps.append({"action": action, "args": realized_args})

        return hydrated_steps

    def _load_patterns(self) -> dict:
        """Loads the pattern store from disk."""
        if os.path.exists(self.store_path):
            try:
                with open(self.store_path, "r") as f:
                    data = json.load(f)
                    return data.get("patterns", {})
            except Exception as e:
                self.logger.error(f"Failed to load pattern store: {e}")
        return {}


# Global instance
retriever = PatternRetriever()