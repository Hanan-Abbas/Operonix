import os
import shutil
import asyncio
from pathlib import Path
from core.event_bus import bus

class FileTool:
    def __init__(self):
        self.name = "file_tool"

    async def run(self, action, args):
        path_str = args.get("path")
        if not path_str:
            return False, "No path provided."

        # Cross-platform path normalization
        safe_path = Path(path_str).resolve()

        # Emit event for the Web Dashboard to show "File Activity"
        await bus.emit("file_op_started", {"action": action, "path": str(safe_path)}, source="file_tool")

        try:
            if action == "write":
                # Run blocking I/O in a separate thread to keep the Bus fast
                return await asyncio.to_thread(self._write_file, safe_path, args.get("data", ""))

            elif action == "append":
                return await asyncio.to_thread(self._append_file, safe_path, args.get("data", ""))

            elif action == "mkdir":
                return await asyncio.to_thread(self._mkdir, safe_path, args.get("exist_ok", True))
            
            elif action == "read":
                return await asyncio.to_thread(self._read_file, safe_path)
            
            elif action == "delete":
                return await asyncio.to_thread(self._delete_item, safe_path)
            
            elif action == "list":
                return await asyncio.to_thread(self._list_directory, safe_path)
            
            elif action == "exists":
                return await asyncio.to_thread(self._check_exists, safe_path)
            
            elif action == "move":
                return await asyncio.to_thread(self._move_item, safe_path, args.get("destination"))

            return False, f"Unknown action: {action}"

        except Exception as e:
            return False, f"File Error: {str(e)}"

    # --- Synchronous worker methods (Wrapped by asyncio.to_thread above) ---

    def _write_file(self, path, data):
        path.parent.mkdir(parents=True, exist_ok=True)
        with open(path, "w", encoding="utf-8") as f:
            f.write(data)
        return True, f"Successfully wrote to {path}"

    def _append_file(self, path, data):
        path.parent.mkdir(parents=True, exist_ok=True)
        with open(path, "a", encoding="utf-8") as f:
            f.write(data)
        return True, f"Successfully appended to {path}"

    def _mkdir(self, path, exist_ok):
        path.mkdir(parents=True, exist_ok=exist_ok)
        return True, f"Directory ready: {path}"

    def _read_file(self, path):
        if not path.exists():
            return False, f"File {path} does not exist."
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
        return True, os.listdir(path)

    def _check_exists(self, path):
        return True, path.exists()

    def _move_item(self, path, destination):
        if not destination:
            return False, "No destination provided."
        dest_path = Path(destination).resolve()
        dest_path.parent.mkdir(parents=True, exist_ok=True)
        shutil.move(str(path), str(dest_path))
        return True, f"Moved to {dest_path}"

# Global instance
file_tool = FileTool()