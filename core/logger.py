import os
import json
import logging
from datetime import datetime
from core.event_bus import bus

class SystemLogger:
    def __init__(self):
        self.log_dir = "logs"
        self._ensure_log_dir()
        
        # Paths to specific log files
        self.action_log = os.path.join(self.log_dir, "actions.log")
        self.error_log = os.path.join(self.log_dir, "errors.log")
        self.decision_log = os.path.join(self.log_dir, "decisions.log")

    def _ensure_log_dir(self):
        """Create the logs directory if it doesn't exist."""
        if not os.path.exists(self.log_dir):
            os.makedirs(self.log_dir)

    async def start(self):
        """Subscribe to all events to record them."""
        bus.subscribe("*", self.handle_event)
        print("📜 System Logger: Online and recording to /logs")

    async def handle_event(self, event):
        """Routes events to the correct file based on their type."""
        timestamp = datetime.now().strftime("%Y-%m-%d %H:%M:%S")
        
        # Prepare the log entry
        log_entry = {
            "timestamp": timestamp,
            "source": event.source,
            "event_type": event.name,
            "data": event.data
        }

        # 1. Categorize the log
        if "error" in event.name.lower():
            self._write_to_file(self.error_log, log_entry)
        
        elif event.name == "context_snapshot_ready":
            # This is our Action History!
            self._write_to_file(self.action_log, log_entry)
            
        elif "decision" in event.name.lower() or "intent" in event.name.lower():
            self._write_to_file(self.decision_log, log_entry)

    def _write_to_file(self, file_path, entry):
        """Appends a JSON entry to the specified log file."""
        try:
            with open(file_path, "a") as f:
                f.write(json.dumps(entry) + "\n")
        except Exception as e:
            print(f"❌ Logger failed to write to {file_path}: {e}")

# Global instance
logger = SystemLogger()