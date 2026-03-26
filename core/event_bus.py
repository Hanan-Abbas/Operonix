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

    async def emit(self, event_name: str, data: Any = None, source: str = "unknown"):
        """Fire an event into the system."""
        event = Event(event_name, data, source)
        await self._queue.put(event)

    async def run(self):
        """The main loop that processes events and notifies listeners."""
        self.logger.info("Event Bus is running...")
        while True:
            event = await self._queue.get()
            
            # Log every event for the dashboard/logs/decisions.log
            print(event) 
            
            if event.name in self.listeners:
                tasks = [
                    self._execute_callback(callback, event) 
                    for callback in self.listeners[event.name]
                ]
                await asyncio.gather(*tasks)
            
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