"""
Timeout Manager — Operonix Graph
────────────────────────────────

Timeout manager for operations, steps, tasks, and system/watchdog.
Per migration plan Phase 8: Cancellation, Timeout & Resource Control
"""
from __future__ import annotations

import logging
import threading
import time
from typing import Dict, Any, Optional, Callable
from datetime import datetime, timedelta, timezone

from migration.domain_contracts import TimeoutConfig, CancellationRequest, CancellationReason

# Compatibility for Python < 3.11
try:
    from datetime import UTC
except ImportError:
    UTC = timezone.utc

logger = logging.getLogger("Graph.TimeoutManager")


class TimeoutManager:
    """Manager for timeout enforcement and cancellation.
    
    Per migration plan Phase 8:
    - operation timeout
    - step timeout
    - task timeout
    - system/watchdog timeout
    """
    
    def __init__(self, timeout_config: Optional[TimeoutConfig] = None):
        """Initialize timeout manager.
        
        Args:
            timeout_config: Timeout configuration. Defaults to default TimeoutConfig.
        """
        self.timeout_config = timeout_config or TimeoutConfig()
        self.active_timeouts: Dict[str, Dict[str, Any]] = {}
        self.timeout_callbacks: Dict[str, Callable] = {}
        self._lock = threading.Lock()
        self._watchdog_thread: Optional[threading.Thread] = None
        self._stop_event = threading.Event()
        self._check_interval = 1.0  # Check every second
        
        logger.info(f"TimeoutManager initialized with config: {self.timeout_config}")
    
    def start_operation_timeout(self, task_id: str, operation_id: str, callback: Callable) -> None:
        """Start timeout for an individual operation.
        
        Args:
            task_id: Task identifier
            operation_id: Operation identifier
            callback: Callback to execute on timeout
        """
        timeout_key = f"{task_id}:{operation_id}"
        timeout_seconds = self.timeout_config.operation_timeout_seconds
        
        with self._lock:
            self.active_timeouts[timeout_key] = {
                "type": "operation",
                "task_id": task_id,
                "operation_id": operation_id,
                "timeout_seconds": timeout_seconds,
                "started_at": datetime.now(UTC),
                "expires_at": datetime.now(UTC) + timedelta(seconds=timeout_seconds)
            }
            self.timeout_callbacks[timeout_key] = callback
        
        logger.debug(f"Started operation timeout: {timeout_key} ({timeout_seconds}s)")
    
    def start_step_timeout(self, task_id: str, step_id: str, callback: Callable) -> None:
        """Start timeout for a graph step.
        
        Args:
            task_id: Task identifier
            step_id: Step identifier
            callback: Callback to execute on timeout
        """
        timeout_key = f"{task_id}:step:{step_id}"
        timeout_seconds = self.timeout_config.step_timeout_seconds
        
        with self._lock:
            self.active_timeouts[timeout_key] = {
                "type": "step",
                "task_id": task_id,
                "step_id": step_id,
                "timeout_seconds": timeout_seconds,
                "started_at": datetime.now(UTC),
                "expires_at": datetime.now(UTC) + timedelta(seconds=timeout_seconds)
            }
            self.timeout_callbacks[timeout_key] = callback
        
        logger.debug(f"Started step timeout: {timeout_key} ({timeout_seconds}s)")
    
    def start_task_timeout(self, task_id: str, callback: Callable) -> None:
        """Start timeout for entire task.
        
        Args:
            task_id: Task identifier
            callback: Callback to execute on timeout
        """
        timeout_key = f"{task_id}:task"
        timeout_seconds = self.timeout_config.task_timeout_seconds
        
        with self._lock:
            self.active_timeouts[timeout_key] = {
                "type": "task",
                "task_id": task_id,
                "timeout_seconds": timeout_seconds,
                "started_at": datetime.now(UTC),
                "expires_at": datetime.now(UTC) + timedelta(seconds=timeout_seconds)
            }
            self.timeout_callbacks[timeout_key] = callback
        
        logger.debug(f"Started task timeout: {timeout_key} ({timeout_seconds}s)")
    
    def start_watchdog_timeout(self, task_id: str, callback: Callable) -> None:
        """Start system/watchdog timeout.
        
        Args:
            task_id: Task identifier
            callback: Callback to execute on timeout
        """
        timeout_key = f"{task_id}:watchdog"
        timeout_seconds = self.timeout_config.system_watchdog_timeout_seconds
        
        with self._lock:
            self.active_timeouts[timeout_key] = {
                "type": "watchdog",
                "task_id": task_id,
                "timeout_seconds": timeout_seconds,
                "started_at": datetime.now(UTC),
                "expires_at": datetime.now(UTC) + timedelta(seconds=timeout_seconds)
            }
            self.timeout_callbacks[timeout_key] = callback
        
        logger.debug(f"Started watchdog timeout: {timeout_key} ({timeout_seconds}s)")
    
    def cancel_timeout(self, timeout_key: str) -> bool:
        """Cancel an active timeout.
        
        Args:
            timeout_key: Timeout key to cancel
            
        Returns:
            True if cancelled, False if not found
        """
        with self._lock:
            if timeout_key in self.active_timeouts:
                del self.active_timeouts[timeout_key]
                if timeout_key in self.timeout_callbacks:
                    del self.timeout_callbacks[timeout_key]
                logger.debug(f"Cancelled timeout: {timeout_key}")
                return True
            return False
    
    def cancel_all_timeouts_for_task(self, task_id: str) -> int:
        """Cancel all timeouts for a task.
        
        Args:
            task_id: Task identifier
            
        Returns:
            Number of timeouts cancelled
        """
        cancelled_count = 0
        with self._lock:
            keys_to_cancel = [key for key in self.active_timeouts if key.startswith(task_id)]
            for key in keys_to_cancel:
                del self.active_timeouts[key]
                if key in self.timeout_callbacks:
                    del self.timeout_callbacks[key]
                cancelled_count += 1
        
        logger.debug(f"Cancelled {cancelled_count} timeouts for task: {task_id}")
        return cancelled_count
    
    def check_timeouts(self) -> list:
        """Check for expired timeouts and execute callbacks.
        
        Returns:
            List of expired timeout keys
        """
        now = datetime.now(UTC)
        expired = []
        
        with self._lock:
            for timeout_key, timeout_info in list(self.active_timeouts.items()):
                if timeout_info["expires_at"] <= now:
                    expired.append(timeout_key)
                    # Execute callback
                    if timeout_key in self.timeout_callbacks:
                        callback = self.timeout_callbacks[timeout_key]
                        try:
                            callback(timeout_key, timeout_info)
                        except Exception as e:
                            logger.error(f"Error executing timeout callback for {timeout_key}: {e}")
                    # Remove expired timeout
                    del self.active_timeouts[timeout_key]
                    if timeout_key in self.timeout_callbacks:
                        del self.timeout_callbacks[timeout_key]
        
        if expired:
            logger.info(f"Expired timeouts: {expired}")
        
        return expired
    
    def start_watchdog(self) -> None:
        """Start the background watchdog thread for timeout checking.
        
        This starts a background thread that periodically checks for expired timeouts
        and executes their callbacks. This is the actual implementation of the
        automatic timeout checking that was previously a stub.
        """
        if self._watchdog_thread is not None and self._watchdog_thread.is_alive():
            logger.warning("Watchdog thread is already running")
            return
        
        self._stop_event.clear()
        self._watchdog_thread = threading.Thread(
            target=self._watchdog_loop,
            name="TimeoutWatchdog",
            daemon=True
        )
        self._watchdog_thread.start()
        logger.info("Timeout watchdog thread started")
    
    def stop_watchdog(self) -> None:
        """Stop the background watchdog thread.
        
        This stops the background thread that checks for expired timeouts.
        """
        if self._watchdog_thread is None:
            logger.warning("No watchdog thread to stop")
            return
        
        self._stop_event.set()
        self._watchdog_thread.join(timeout=5.0)
        
        if self._watchdog_thread.is_alive():
            logger.warning("Watchdog thread did not stop gracefully")
        else:
            logger.info("Timeout watchdog thread stopped")
        
        self._watchdog_thread = None
    
    def _watchdog_loop(self) -> None:
        """Watchdog loop that periodically checks for expired timeouts.
        
        This runs in a background thread and calls check_timeouts() at regular intervals.
        """
        logger.info("Watchdog loop started")
        
        while not self._stop_event.is_set():
            try:
                # Check for expired timeouts
                expired = self.check_timeouts()
                
                # Sleep for the check interval
                self._stop_event.wait(self._check_interval)
                
            except Exception as e:
                logger.error(f"Error in watchdog loop: {e}")
                # Sleep briefly to avoid tight error loop
                self._stop_event.wait(self._check_interval)
        
        logger.info("Watchdog loop stopped")
    
    def get_active_timeouts(self, task_id: Optional[str] = None) -> list:
        """Get active timeouts.
        
        Args:
            task_id: Optional task ID to filter by
            
        Returns:
            List of active timeout info
        """
        with self._lock:
            if task_id:
                return [info for key, info in self.active_timeouts.items() if key.startswith(task_id)]
            return list(self.active_timeouts.values())


# Global timeout manager instance
_timeout_manager: Optional[TimeoutManager] = None


def get_timeout_manager() -> TimeoutManager:
    """Get the global timeout manager instance.
    
    Returns:
        TimeoutManager instance
    """
    global _timeout_manager
    
    if _timeout_manager is None:
        _timeout_manager = TimeoutManager()
    
    return _timeout_manager
