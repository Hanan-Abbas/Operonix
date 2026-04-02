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
        # This works perfectly because our new event bus supports '*'!
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

        event_lower = event.name.lower()

        # Dynamic sorting based on actual AI pipeline events
        
        # 🔴 1. Error Log
        if any(x in event_lower for x in ["error", "fail", "alert"]):
            self._write_to_file(self.error_log, log_entry)
        
        # 🟡 2. Decision Log (The Brain's reasoning steps)
        elif any(x in event_lower for x in ["intent", "mapped", "reasoning", "planning", "dispatched"]):
            self._write_to_file(self.decision_log, log_entry)
            
        # 🟢 3. Action Log (Actual operations that change the environment)
        elif any(x in event_lower for x in ["execution", "action", "executed", "snapshot"]):
            self._write_to_file(self.action_log, log_entry)

    def _write_to_file(self, file_path, entry):
        """Appends a JSON entry to the specified log file."""
        try:
            with open(file_path, "a") as f:
                f.write(json.dumps(entry) + "\n")
        except Exception as e:
            # Standard print used here to avoid infinite loops on failure
            print(f"❌ Logger failed to write to {file_path}: {e}")

# 🟢 FIX: Renamed global instance to avoid terminal-crashing collisions!
sys_logger = SystemLogger()