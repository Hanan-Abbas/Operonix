import os
import shutil
from pathlib import Path
from core.event_bus import bus

class FileTool:
    def __init__(self):
        self.name = "file_tool"

    async def run(self, action, args):
        """
        Main entry point called by the Executor.
        Actions: write, read, delete, list, exists, move
        """
        path = args.get("path")
        if not path:
            return False, "No path provided for file operation."

        # Cross-platform path normalization
        safe_path = Path(path).resolve()

        try:
            if action == "write":
                return self._write_file(safe_path, args.get("data", ""))
            
            elif action == "read":
                return self._read_file(safe_path)
            
            elif action == "delete":
                return self._delete_item(safe_path)
            
            elif action == "list":
                return self._list_directory(safe_path)
            
            elif action == "exists":
                return self._check_exists(safe_path)
            
            elif action == "move":
                return self._move_item(safe_path, args.get("destination"))

            return False, f"Unknown action: {action}"

        except Exception as e:
            return False, f"File Error: {str(e)}"

    