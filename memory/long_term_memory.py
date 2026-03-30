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

    