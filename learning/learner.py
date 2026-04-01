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

    