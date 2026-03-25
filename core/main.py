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