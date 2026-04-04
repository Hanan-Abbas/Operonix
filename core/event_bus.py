import asyncio
import logging
import fnmatch
from datetime import datetime
from typing import Any, Callable, Dict, List, Optional


class Event:

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

        # The main thread's loop will be stored here
        self._event_loop: Optional[asyncio.AbstractEventLoop] = None
        self._queue = asyncio.PriorityQueue()

    def subscribe(self, event_pattern: str, callback: Callable):
        if event_pattern not in self.listeners:
            self.listeners[event_pattern] = []
        if callback not in self.listeners[event_pattern]:
            self.listeners[event_pattern].append(callback)
            self.logger.info(f"Subscribed to pattern: {event_pattern}")

    async def emit(self, event_type: str, data: Any = None, source: str = None):
        """Pushes an event into the priority queue."""
        event = Event(event_type, data, source)

        priority = 50
        event_lower = event_type.lower()
        if any(
            x in event_lower
            for x in ["stop", "abort", "security", "fail", "alert"]
        ):
            priority = 10
        elif any(
            x in event_lower for x in ["log", "metric", "update", "state"]
        ):
            priority = 90

        await self._queue.put((priority, event))

    def publish(self, event_type: str, data: Any = None, source: str = None):
        """🟢 100% Thread-Safe & Non-Blocking!

        Can be called from worker threads OR the main loop.
        """
        # If we are already in the main async thread, just use emit
        try:
            loop = asyncio.get_running_loop()
            if loop == self._event_loop:
                loop.create_task(self.emit(event_type, data, source))
                return
        except RuntimeError:
            pass

        # 🚀 If called from openWakeWord's thread, we inject it directly!
        if self._event_loop is not None and self._event_loop.is_running():
            self._event_loop.call_soon_threadsafe(
                # This schedules the coroutine safely without blocking
                lambda: asyncio.create_task(
                    self.emit(event_type, data, source)
                )
            )
        else:
            self.logger.warning(
                f"Dropped event '{event_type}': Event loop is not running yet."
            )

    async def run(self):
        """The main loop that processes events."""
        # Capture the main loop right as it starts running
        self._event_loop = asyncio.get_running_loop()
        self.logger.info("Event Bus is running...")

        while True:
            priority, event = await self._queue.get()
            print(f"[Priority {priority}] {event}")

            matched_listeners = []
            for pattern, callbacks in self.listeners.items():
                if fnmatch.fnmatch(event.name, pattern):
                    matched_listeners.extend(callbacks)

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


bus = EventBus()