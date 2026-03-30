import logging
import time
from core.config import settings
from core.event_bus import bus


class SessionMemory:
    """🧠 Short-term session memory for the AI OS.

    Keeps track of active tasks, recent successful actions, and UI context.
    Acts as the ground truth for what the agent is currently doing.
    """

    def __init__(self):
        self.logger = logging.getLogger("SessionMemory")

        # In-memory storage for the active session
        self.active_tasks = {}  # {task_id: {data}}
        self.action_history = []  # List of past steps executed
        self.context_snapshots = {}  # {window_title: element_tree}

        # Configuration limits to prevent memory bloating
        self.max_history_items = 50

    async def start(self):
        """Subscribe to the event bus to passively listen and remember."""
        # Listen to execution updates
        bus.subscribe("execution_step_started", self._remember_step_start)
        bus.subscribe("execution_step_success", self._remember_step_success)
        bus.subscribe("task_completed", self._archive_task)
        bus.subscribe("task_failed", self._mark_task_failed)

        self.logger.info(
            "📦 Session Memory: Online and passively recording execution history."
        )

    