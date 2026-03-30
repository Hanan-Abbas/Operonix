import asyncio
import logging
import os
from core.event_bus import bus
from core.event_bus import Event
# We import these so the listener can pass data to the repair engine
from debugging.error_parser import ErrorParser
from debugging.auto_fix import AutoFixer

class ErrorListener:
    """
    Listens to the Event Bus for 'system_error' events.
    Handles both global uncaught crashes and localized try/except errors.
    """
    def __init__(self):
        self.logger = logging.getLogger("ErrorListener")
        self.parser = ErrorParser()
        self.fixer = AutoFixer()
        self.is_running = False

    