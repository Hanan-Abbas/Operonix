import asyncio
import logging
import fnmatch
from datetime import datetime
from typing import Any, Callable, Dict, List


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
        
        # 🔄 UPGRADE: Changed to PriorityQueue to handle high-priority safety tasks first.
        self._queue = asyncio.PriorityQueue()

    def subscribe(self, event_pattern: str, callback: Callable):
        """Subscribes to an event pattern. Supports wildcards like 'file_*'."""
        if event_pattern not in self.listeners:
            self.listeners[event_pattern] = []
        if callback not in self.listeners[event_pattern]:
            self.listeners[event_pattern].append(callback)
            self.logger.info(f"Subscribed to pattern: {event_pattern}")

    async def emit(self, event_type: str, data: Any = None, source: str = None):
        """Pushes an event into the queue dynamically with priority inference."""
        event = Event(event_type, data, source)
        
        # 🔄 UPGRADE: Zero-hardcoded priority inference based on words in the event name
        priority = 50 # Default middle-ground priority
        
        event_lower = event_type.lower()
        if any(x in event_lower for x in ["stop", "abort", "security", "fail", "alert"]):
            priority = 10 # High priority (Lower number = processed first in asyncio.PriorityQueue)
        elif any(x in event_lower for x in ["log", "metric", "update", "state"]):
            priority = 90 # Low priority background noise
            
        # Put the event in the queue with its priority
        await self._queue.put((priority, event))

    def publish(self, event_type: str, data: Any = None, source: str = None):
        """A synchronous wrapper to emit events from normal, non-async functions."""
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
            priority, event = await self._queue.get()

            # Print for immediate debugging
            print(f"[Priority {priority}] {event}")

            # 🔄 UPGRADE: Dynamic Pattern Matching! 
            # Now modules can subscribe to 'file_*' instead of hardcoding exact matches.
            matched_listeners = []
            for pattern, callbacks in self.listeners.items():
                if fnmatch.fnmatch(event.name, pattern):
                    matched_listeners.extend(callbacks)

            # Fire them off in the background asynchronously
            for callback in matched_listeners:
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