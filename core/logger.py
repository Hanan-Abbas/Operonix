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

    