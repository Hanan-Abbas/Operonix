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
    self-healing, and firing recovery events.
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

        