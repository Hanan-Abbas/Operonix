import asyncio
import logging
from datetime import datetime
from typing import Any, Callable, Dict, List, Optional


class Event:
    """Standardized event structure for the entire OS."""

    def __init__(self, name: str, data: Any = None, source: str = "system"):
        self.name = name
        self.data = data
        self.source = source or "system"
        self.timestamp = datetime.now().isoformat()

    def __str__(self):
        return f"[{self.timestamp}] {self.source} -> {self.name}: {self.data}"


class EventBus:

    def __init__(self):
        self.listeners: Dict[str, List[Callable]] = {}
        self.logger = logging.getLogger("EventBus")
        # 1. Initialize the queue properly
        self._queue = asyncio.Queue()

    def subscribe(self, event_name: str, callback: Callable):
        if event_name not in self.listeners:
            self.listeners[event_name] = []
        if callback not in self.listeners[event_name]:
            self.listeners[event_name].append(callback)
            self.logger.info(f"Subscribed to {event_name}")

    async def emit(self, event_type: str, data: Any = None, source: str = None):
        """Pushes an event into the queue.

        This is the correct way to feed the 'run' loop.
        """
        event = Event(event_type, data, source)
        # Put the event in the queue for the background worker to handle
        await self._queue.put(event)

    def publish(self, event_type: str, data: Any = None, source: str = None):
        """A synchronous wrapper to emit events from normal, non-async def

        functions.
        """
        try:
            loop = asyncio.get_running_loop()
            loop.create_task(self.emit(event_type, data, source))
        except RuntimeError:
            self.logger.warning(
                "EventBus publish failed: No running event loop."
            )

    async def run(self):
        """The main loop that processes events and notifies listeners."""
        self.logger.info("Event Bus is running...")
        while True:
            # Wait for an event to be added via emit()
            event = await self._queue.get()

            # Print for immediate debugging
            print(event)

            # Gather normal listeners and wildcard (*) listeners
            listeners = self.listeners.get(
                event.name, []
            ) + self.listeners.get("*", [])

            for callback in listeners:
                # Use create_task so slow listeners don't hold up the entire queue
                asyncio.create_task(self._execute_callback(callback, event))

            self._queue.task_done()

    async def _execute_callback(self, callback: Callable, event: Event):
        try:
            if asyncio.iscoroutinefunction(callback):
                await callback(event)
            else:
                callback(event)
        except Exception as e:
            self.logger.error(f"Error in listener for {event.name}: {e}")


# Global instance
bus = EventBus()