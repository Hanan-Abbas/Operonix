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

    async def validate(self, tool_name, action, args):
        """
        Returns (True, None) if safe, (False, error_msg) if dangerous.
        """
        # 1. Check Shell Commands
        if tool_name == "shell_tool" and "command" in args:
            cmd = args["command"]
            for pattern in self.forbidden_commands:
                if re.search(pattern, cmd):
                    return False, f"DANGEROUS_COMMAND: {cmd}"

        # 2. Check File Paths
        if "path" in args:
            path = str(args["path"])
            for forbidden in self.forbidden_paths:
                if forbidden in path:
                    return False, f"FORBIDDEN_PATH_ACCESS: {path}"

        return True, "Safe"

tool_validator = ToolValidator()