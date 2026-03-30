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

    async def start(self):
        """Starts the listener and subscribes to the Event Bus."""
        self.logger.info("Error Listener is starting up...")
        
        # Subscribe to the 'system_error' event we set up in ErrorHandler
        bus.subscribe("system_error", self.on_error_received)
        
        # Also subscribe to wildcard if you want to track general health, 
        # but sticking to 'system_error' keeps it focused.
        
        self.is_running = True
        self.logger.info("Error Listener is now active and listening to the Event Bus.")

    async def on_error_received(self, event: Event):
        """
        Callback triggered whenever an error is published to the bus.
        Works for both sync/async global hooks AND try/except blocks!
        """
        try:
            error_payload = event.data
            self.logger.warning(f"🚨 New error intercepted from source '{event.source}'!")

            # 1. Hand the raw JSON payload to the parser
            # This extracts the exact file, line number, and error type
            parsed_report = self.parser.parse(error_payload)
            
            # 2. Log the receipt for the dashboard to see
            print(f"🛠️ [Self-Healing] Analyzing failure in function: {parsed_report.get('function')}")

            # 3. Trigger the Auto-Fixer!
            # We run this as a background task so it doesn't block the Event Bus queue
            asyncio.create_task(self.fixer.attempt_fix(parsed_report))

        except Exception as e:
            self.logger.error(f"Critical failure inside ErrorListener itself: {e}")

    def stop(self):
        """Unsubscribe and power down."""
        self.is_running = False
        self.logger.info("Error Listener shut down.")

# Global instance to be imported in main.py
error_listener = ErrorListener()