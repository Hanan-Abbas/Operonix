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

    async def _remember_step_start(self, event):
        """Records when a task begins moving."""
        data = event.data
        task_id = data.get("task_id")

        if task_id not in self.active_tasks:
            self.active_tasks[task_id] = {
                "start_time": time.time(),
                "steps": [],
                "status": "in_progress",
            }

        self.active_tasks[task_id]["steps"].append(
            {
                "step_index": data.get("step_index"),
                "action": data.get("action"),
                "status": "running",
                "timestamp": time.time(),
            }
        )

    async def _remember_step_success(self, event):
        """Updates the active task with the results of a specific action."""
        data = event.data
        task_id = data.get("task_id")
        step_index = data.get("step_index")

        if task_id in self.active_tasks:
            for step in self.active_tasks[task_id]["steps"]:
                if step["step_index"] == step_index:
                    step["status"] = "success"
                    step["result"] = data.get("result")
                    break

            # Add to rolling global history
            self._add_to_history(
                {
                    "task_id": task_id,
                    "action": step.get("action"),
                    "result": data.get("result"),
                    "timestamp": time.time(),
                }
            )

    async def _archive_task(self, event):
        """Marks a full task as completed and moves it to a cold storage state

        or dumps to a log if needed.
        """
        task_id = event.data.get("task_id")
        if task_id in self.active_tasks:
            self.active_tasks[task_id]["status"] = "completed"
            self.active_tasks[task_id]["end_time"] = time.time()
            self.logger.debug(f"Archived memory for completed task: {task_id}")

            # Here is the bridge to your learning system!
            # Once a task is successfully completed, we can broadcast it for the learner.
            bus.publish(
                "task_memory_archived",
                self.active_tasks[task_id],
                source="session_memory",
            )

    async def _mark_task_failed(self, event):
        """Keeps records of what went wrong to feed the debugging and learning

        system.
        """
        data = event.data
        task_id = data.get("task_id")

        if task_id in self.active_tasks:
            self.active_tasks[task_id]["status"] = "failed"
            self.active_tasks[task_id]["error"] = data.get("error")
            self.logger.warning(
                f"Memory flagged task {task_id} as failed: {data.get('error')}"
            )

    def _add_to_history(self, action_dict):
        """Adds a successful action to the rolling window list."""
        self.action_history.append(action_dict)
        # Prevent memory overflow
        if len(self.action_history) > self.max_history_items:
            self.action_history.pop(0)

    def get_task_history(self, task_id):
        """Returns the steps executed for a specific task."""
        return self.active_tasks.get(task_id, {})

    def get_recent_actions(self, count=5):
        """Returns the last N actions performed across the entire system."""
        return self.action_history[-count:]


# Global instance
session_memory = SessionMemory()