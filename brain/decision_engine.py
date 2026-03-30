import asyncio
import logging
from typing import Any, Dict, List
from core.config import settings
from core.event_bus import bus


class DecisionEngine:
    """The traffic cop of the AI.

    Prioritizes tasks, resolves conflicts between simultaneous intents, and
    determines if the system has the bandwidth to execute a plan.
    """

    def __init__(self):
        self.logger = logging.getLogger("DecisionEngine")
        self.task_queue = asyncio.PriorityQueue()
        self.active_tasks = {}

        # Priority matrix: Higher number = executes first
        self.priority_matrix = {
            "emergency_stop": 100,
            "security_alert": 90,
            "user_voice_command": 50,
            "ui_interaction": 30,
            "file_ops": 20,
            "web_search": 10,
            "background_learning": 1,
        }

    async def start(self):
        """Subscribe to validated intents and start the processing loop."""
        # We listen to the intent parser
        bus.subscribe("intent_validated", self.enqueue_task)

        # Start the background worker that feeds the planner
        asyncio.create_task(self._process_queue())
        self.logger.info(
            "🧠 Decision Engine: Active. Task prioritization loop running."
        )

    