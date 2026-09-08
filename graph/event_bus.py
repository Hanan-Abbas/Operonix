"""
Event Bus — Operonix Graph
──────────────────────────

Event bus for publishing observability events.
Per migration plan Phase 9: Observability & Execution Trace

EventBus publishes observability events; it does not become the task state machine again.
"""
from __future__ import annotations

import logging
from typing import Dict, Any, Callable, List, Optional
from datetime import datetime

from migration.domain_contracts import ObservabilityEvent

logger = logging.getLogger("Graph.EventBus")


class EventBus:
    """Event bus for publishing observability events.
    
    Per migration plan Phase 9: EventBus publishes observability events;
    it does not become the task state machine again.
    """
    
    def __init__(self):
        """Initialize event bus."""
        self.subscribers: Dict[str, List[Callable]] = {}
        self.event_history: List[ObservabilityEvent] = []
        
        logger.info("EventBus initialized")
    
    def subscribe(self, event_type: str, callback: Callable) -> None:
        """Subscribe to events of a specific type.
        
        Args:
            event_type: Type of event to subscribe to
            callback: Callback function to execute on event
        """
        if event_type not in self.subscribers:
            self.subscribers[event_type] = []
        
        self.subscribers[event_type].append(callback)
        
        logger.debug(f"Subscribed to event type: {event_type}")
    
    def unsubscribe(self, event_type: str, callback: Callable) -> bool:
        """Unsubscribe from events of a specific type.
        
        Args:
            event_type: Type of event to unsubscribe from
            callback: Callback function to remove
            
        Returns:
            True if unsubscribed, False if not found
        """
        if event_type not in self.subscribers:
            return False
        
        if callback in self.subscribers[event_type]:
            self.subscribers[event_type].remove(callback)
            logger.debug(f"Unsubscribed from event type: {event_type}")
            return True
        
        return False
    
    def publish(self, event: ObservabilityEvent) -> None:
        """Publish an event to all subscribers.
        
        Args:
            event: ObservabilityEvent to publish
        """
        # Add to history
        self.event_history.append(event)
        
        # Notify subscribers
        if event.event_type in self.subscribers:
            for callback in self.subscribers[event.event_type]:
                try:
                    callback(event)
                except Exception as e:
                    logger.error(f"Error in event subscriber for {event.event_type}: {e}")
        
        logger.debug(f"Published event: {event.event_type} for task {event.task_id}")
    
    def create_and_publish(self, task_id: str, event_type: str, data: Dict[str, Any], metadata: Optional[Dict[str, Any]] = None) -> ObservabilityEvent:
        """Create and publish an event in one call.
        
        Args:
            task_id: Task identifier
            event_type: Type of event
            data: Event data
            metadata: Optional metadata
            
        Returns:
            ObservabilityEvent that was published
        """
        event = ObservabilityEvent(
            task_id=task_id,
            event_type=event_type,
            data=data,
            metadata=metadata or {}
        )
        
        self.publish(event)
        
        return event
    
    def get_event_history(self, task_id: Optional[str] = None, event_type: Optional[str] = None) -> List[ObservabilityEvent]:
        """Get event history.
        
        Args:
            task_id: Optional task ID to filter by
            event_type: Optional event type to filter by
            
        Returns:
            List of ObservabilityEvent
        """
        events = self.event_history
        
        if task_id:
            events = [e for e in events if e.task_id == task_id]
        
        if event_type:
            events = [e for e in events if e.event_type == event_type]
        
        return events
    
    def clear_history(self, task_id: Optional[str] = None) -> int:
        """Clear event history.
        
        Args:
            task_id: Optional task ID to clear history for
            
        Returns:
            Number of events cleared
        """
        if task_id:
            before_count = len(self.event_history)
            self.event_history = [e for e in self.event_history if e.task_id != task_id]
            cleared_count = before_count - len(self.event_history)
        else:
            cleared_count = len(self.event_history)
            self.event_history = []
        
        logger.info(f"Cleared {cleared_count} events from history")
        
        return cleared_count


# Global event bus instance
_event_bus: Optional[EventBus] = None


def get_event_bus() -> EventBus:
    """Get the global event bus instance.
    
    Returns:
        EventBus instance
    """
    global _event_bus
    
    if _event_bus is None:
        _event_bus = EventBus()
    
    return _event_bus
