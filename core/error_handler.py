import inspect
import logging
import os
import traceback
from datetime import datetime
from typing import Any, Callable, Dict, Optional


class AIOSException(Exception):
    """Base exception class for the AI OS Agent."""

    def __init__(self, message: str, component: str, details: Optional[Dict] = None):
        super().__init__(message)
        self.message = message
        self.component = component
        self.details = details or {}
        self.timestamp = datetime.utcnow().isoformat()


class ErrorHandler:
    """Global error handler designed for a self-evolving AI system.

    Maintains uptime by catching failures, logging deep tracebacks for AI
    self-healing, and firing recovery events to the EventBus.
    """

    def __init__(self, event_bus=None, logger=None):
        # Fallback to standard logging if core/logger.py isn't loaded yet
        self.logger = logger or logging.getLogger("core.error_handler")
        self.event_bus = event_bus

        # Resolve log paths based on your project structure
        self.error_log_path = os.path.abspath(
            os.path.join(
                os.path.dirname(__file__), "..", "logs", "errors.log"
            )
        )

    def handle_error(
        self,
        error: Exception,
        component: str = "unknown",
        context: Optional[Dict[str, Any]] = None,
    ) -> Dict[str, Any]:
        """The primary method to call when any try/except block fails in the system."""
        context = context or {}

        # 1. Gather deep diagnostic data for the debugging/auto_fix system
        error_type = type(error).__name__
        tb_str = traceback.format_exc()

        # Try to guess the function name that caused the crash
        caller = inspect.currentframe().f_back
        func_name = caller.f_code.co_name if caller else "unknown"

        error_data = {
            "timestamp": datetime.utcnow().isoformat(),
            "component": component,
            "function": func_name,
            "error_type": error_type,
            "message": str(error),
            "traceback": tb_str,
            "context": context,
        }

        # 2. Log it specifically where the AI or dashboard can read it
        self.logger.error(
            f"[{component.upper()}] Crash in {func_name}: {error_type} - {error}"
        )
        self._write_to_error_log(error_data)

        # 3. Notify the system via the updated Event Bus
        if self.event_bus:
            # Using the synchronous 'publish' method we added to the EventBus
            # to safely inject the event into the queue from anywhere.
            self.event_bus.publish(
                event_type="system_error",
                data=error_data,
                source=f"error_handler/{component}",
            )

        # 4. Return an execution receipt the orchestrator/executor can understand
        return {
            "status": "error",
            "error_id": error_data["timestamp"],
            "recoverable": self._is_recoverable(error),
            "summary": f"{error_type}: {str(error)}",
        }

    def _write_to_error_log(self, data: Dict[str, Any]) -> None:
        """Appends the massive JSON stack trace to logs/errors.log."""
        try:
            os.makedirs(os.path.dirname(self.error_log_path), exist_ok=True)
            import json

            with open(self.error_log_path, "a") as f:
                f.write(json.dumps(data) + "\n")
        except Exception as e:
            # If the logger itself fails, print to terminal as last resort
            print(f"CRITICAL: Error handler could not write to log file: {e}")

    def _is_recoverable(self, error: Exception) -> bool:
        """Determines if the system should try to push forward or completely stop."""
        unrecoverable_errors = [
            SystemExit,
            KeyboardInterrupt,
            MemoryError,
        ]
        return not any(isinstance(error, e) for e in unrecoverable_errors)


def catch_and_handle(component_name: str):
    """Decorator to automatically wrap functions in the error handler."""

    def decorator(func: Callable):
        async def async_wrapper(*args, **kwargs):
            try:
                if inspect.iscoroutinefunction(func):
                    return await func(*args, **kwargs)
            except Exception as e:
                print(f"Async Error in {component_name}: {e}")
                raise e

        def sync_wrapper(*args, **kwargs):
            try:
                return func(*args, **kwargs)
            except Exception as e:
                print(f"Sync Error in {component_name}: {e}")
                raise e

        return async_wrapper if inspect.iscoroutinefunction(func) else sync_wrapper

    return decorator