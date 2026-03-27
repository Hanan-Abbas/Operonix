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

    def _write_file(self, path, data):
        # Ensure directory exists before writing
        path.parent.mkdir(parents=True, exist_ok=True)
        with open(path, "w", encoding="utf-8") as f:
            f.write(data)
        return True, f"Successfully wrote to {path}"

    def _read_file(self, path):
        if not path.exists():
            return False, "File does not exist."
        with open(path, "r", encoding="utf-8") as f:
            return True, f.read()

    def _delete_item(self, path):
        if not path.exists():
            return False, "Target does not exist."
        if path.is_dir():
            shutil.rmtree(path)
        else:
            path.unlink()
        return True, f"Deleted {path}"

    def _list_directory(self, path):
        if not path.is_dir():
            return False, "Path is not a directory."
        items = os.listdir(path)
        return True, items

    def _check_exists(self, path):
        return True, path.exists()

    def _move_item(self, path, destination):
        if not destination:
            return False, "No destination provided."
        dest_path = Path(destination).resolve()
        dest_path.parent.mkdir(parents=True, exist_ok=True)
        shutil.move(str(path), str(dest_path))
        return True, f"Moved {path} to {dest_path}"

# Global instance
file_tool = FileTool()