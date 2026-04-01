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

    