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

    async def enqueue_task(self, event):
        """Receives a validated intent and places it in the priority queue."""
        task_data = event.data
        intent = task_data.get("intent")
        task_id = task_data.get("task_id")

        # Determine priority based on the intent type
        priority_score = self._calculate_priority(intent, task_data)

        # PriorityQueue in python sorts lowest-first, so we invert the score
        # A score of 100 becomes -100 (so it comes out of the queue first)
        await self.task_queue.put((-priority_score, task_data))

        self.logger.info(
            f"📥 Task [{task_id}] ({intent}) queued with priority score: {priority_score}"
        )

    def _calculate_priority(
        self, intent: str, task_data: Dict[str, Any]
    ) -> int:
        """Calculates a numeric priority score for an incoming task."""
        # 1. Base priority from matrix
        score = self.priority_matrix.get(intent, 10)

        # 2. Boost if it's directly from the user's active session
        if task_data.get("source") == "user_foreground":
            score += 25

        # 3. Penalize if the system is currently under heavy load
        # (This links beautifully with your monitoring/performance_monitor.py later!)
        return score

    