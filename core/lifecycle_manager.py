import asyncio
import logging
import signal
import sys
from core.config import settings
from core.event_bus import bus

logger = logging.getLogger("LifecycleManager")


class LifecycleManager:
    """Manages the startup, graceful shutdown, and emergency recovery of the AI

    OS.
    """

    def __init__(self):
        self.is_running = False
        self._background_tasks = set()

    