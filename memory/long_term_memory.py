import json
import logging
import os
import time
from core.config import settings
from core.event_bus import bus


class LongTermMemory:
    """📦 Long-Term Memory for the AI OS.

    Listens for archived tasks from SessionMemory and flushes them to persistent
    disk storage. Provides search/retrieval for the learning and planning
    systems.
    """

    def __init__(self):
        self.logger = logging.getLogger("LongTermMemory")

        # Define the file path where successful tasks are permanently stored
        self.storage_dir = os.path.join("memory", "stores")
        self.history_file = os.path.join(self.storage_dir, "task_history.jsonl")

    async def start(self):
        """Ensure storage directories exist and subscribe to the archive

        event.
        """
        # Create the storage folder if it doesn't exist
        os.makedirs(self.storage_dir, exist_ok=True)

        # Listen to SessionMemory when it finishes archiving a successful task
        bus.subscribe("task_memory_archived", self.save_task_to_disk)

        self.logger.info(
            f"📦 Long-Term Memory: Online. Persisting to {self.history_file}"
        )

    async def save_task_to_disk(self, event):
        """Appends a completed task snapshot to a JSON lines file on the disk."""
        task_data = event.data
        task_id = task_data.get("task_id")

        # We only want to memorize successful or completed tasks for long-term optimization
        if task_data.get("status") != "completed":
            self.logger.debug(
                f"Skipping long-term storage for task {task_id} (Status: {task_data.get('status')})"
            )
            return

        # Structure the data cleanly for future search queries
        record = {
            "task_id": task_id,
            "timestamp": time.time(),
            "steps_count": len(task_data.get("steps", [])),
            "data": task_data,
        }

        try:
            # Append as a single line in a JSONL file (very fast, prevents corrupting the whole file)
            with open(self.history_file, "a", encoding="utf-8") as f:
                f.write(json.dumps(record) + "\n")

            self.logger.info(
                f"💾 Long-Term Memory: Successfully persisted task [{task_id}] to disk."
            )

            # Let the learning system know there is fresh data to analyze!
            bus.publish(
                "long_term_memory_updated",
                {"task_id": task_id},
                source="long_term_memory",
            )

        except OSError as e:
            self.logger.error(
                f"Failed to write task {task_id} to long-term storage: {e}"
            )

    