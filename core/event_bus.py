import asyncio
import logging
from typing import Any, Callable, Dict, List, Optional
from datetime import datetime

class Event:
    """Standardized event structure for the entire OS."""
    def __init__(self, name: str, data: Any = None, source: str = "system"):
        self.name = name
        self.data = data
        self.source = source
        self.timestamp = datetime.now().isoformat()

    def __str__(self):
        return f"[{self.timestamp}] {self.source} -> {self.name}: {self.data}"

class EventBus:
    def __init__(self):
        self.listeners: Dict[str, List[Callable]] = {}
        self.logger = logging.getLogger("EventBus")
        self._queue = asyncio.Queue()

    def subscribe(self, event_name: str, callback: Callable):
        """Register a function to run when a specific event occurs."""
        if event_name not in self.listeners:
            self.listeners[event_name] = []
        self.listeners[event_name].append(callback)
        self.logger.info(f"Subscribed to {event_name}")

    