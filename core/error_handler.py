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


