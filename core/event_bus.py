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

    def __lt__(self, other):
        # Tie-breaker: Compare timestamps if priorities are equal
        return self.timestamp < other.timestamp

    # ── Dict-compatibility shims ──────────────────────────────────────────────
    # Many listeners were written to call event.get("key") or event["key"]
    # as if the event were a plain dict.  These shims delegate to self.data
    # so those callers work without modification.

    def get(self, key: str, default: Any = None) -> Any:
        """Delegate .get() to the event's data dict (or return default)."""
        if isinstance(self.data, dict):
            return self.data.get(key, default)
        return default

    def __getitem__(self, key: str) -> Any:
        if isinstance(self.data, dict):
            return self.data[key]
        raise KeyError(key)

    def __contains__(self, key: str) -> bool:
        if isinstance(self.data, dict):
            return key in self.data
        return False


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
        """100% Thread-safe event publishing."""
        
        # Guard against None event loop
        if self._event_loop is None:
            self.logger.warning(f"Event loop not initialized yet. Dropping event '{event_type}'")
            return
        
        if not self._event_loop.is_running():
            self.logger.warning(f"Event loop not running. Dropping event '{event_type}'")
            return
        
        try:
            # Check if we're already in the event loop thread
            current_loop = asyncio.get_running_loop()
            if current_loop == self._event_loop:
                # Same thread - direct task creation
                current_loop.create_task(self.emit(event_type, data, source))
                return
        except RuntimeError:
            # We're in a different thread
            pass
        
        # Different thread - use thread-safe method
        try:
            self._event_loop.call_soon_threadsafe(
                self._schedule_event,
                event_type, data, source
            )
        except RuntimeError as e:
            self.logger.error(f"Failed to schedule event '{event_type}': {e}")

    def _schedule_event(self, event_type, data, source):
        """Helper to schedule event from foreign thread."""
        try:
            asyncio.create_task(self.emit(event_type, data, source))
        except Exception as e:
            self.logger.error(f"Failed to emit event '{event_type}': {e}")

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
        # Guard: if the callback is bound to a Qt object, that object may have
        # been destroyed (C++ layer deleted) while the Python wrapper still
        # exists. Attempting to call it raises RuntimeError with the message
        # "wrapped C/C++ object … has been deleted".  We detect this by
        # checking __self__ on bound methods and catching that specific error.
        bound_obj = getattr(callback, "__self__", None)
        if bound_obj is not None:
            # isValid() covers QObject subclasses; sip/PyQt5 objects expose it.
            is_valid_fn = getattr(bound_obj, "isValid", None) or getattr(bound_obj, "sip_isdeleted", None)
            if is_valid_fn is not None:
                try:
                    if callable(is_valid_fn) and not is_valid_fn():
                        self.logger.debug(
                            "Skipping dead Qt callback for %s — unsubscribing.", event.name
                        )
                        self._unsubscribe_dead(callback)
                        return
                except Exception:
                    pass

        try:
            if asyncio.iscoroutinefunction(callback):
                await callback(event)
            else:
                callback(event)
        except RuntimeError as e:
            err_str = str(e)
            if "wrapped C/C++ object" in err_str and "has been deleted" in err_str:
                self.logger.debug(
                    "Dead Qt object in listener for %s — removing callback.", event.name
                )
                self._unsubscribe_dead(callback)
            else:
                self.logger.error(f"Error in listener for {event.name}: {e}")
        except Exception as e:
            self.logger.error(f"Error in listener for {event.name}: {e}")

    def _unsubscribe_dead(self, callback: Callable) -> None:
        """Remove a callback that belongs to a destroyed object from all patterns."""
        for pattern in list(self.listeners.keys()):
            try:
                self.listeners[pattern].remove(callback)
            except ValueError:
                pass

bus = EventBus()