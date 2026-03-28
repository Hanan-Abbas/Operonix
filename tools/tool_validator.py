import re

class ToolValidator:
    def __init__(self):
        # Forbidden patterns for shell and file operations
        self.forbidden_commands = [
            r"rm\s+-rf\s+/", 
            r"mkfs", 
            r"shutdown", 
            r"format\s+.:"
        ]
        self.forbidden_paths = [
            "/etc/shadow", 
            "/etc/passwd", 
            "C:\\Windows\\System32"
        ]

    