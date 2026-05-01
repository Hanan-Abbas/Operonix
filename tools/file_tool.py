from __future__ import annotations

import asyncio
import logging
import os
import shutil
from pathlib import Path

from core.event_bus import bus

logger = logging.getLogger("FileTool")

# Maps intent/action strings → internal handler keys.
# This means the executor can pass either the intent name ("create_dir")
# or the short alias ("mkdir") and both route correctly — no more
# "Unknown action" fallthrough and no double-nesting from a second
# dir_name append.
_ACTION_ALIASES: dict[str, str] = {
    "write_file":  "write",
    "append_file": "append",
    "read_file":   "read",
    "delete_file": "delete",
    "list_dir":    "list",
    "create_dir":  "mkdir",   # FIX: intent name now maps to mkdir handler
    "delete_dir":  "rmdir",
    "move_file":   "move",
    "exists":      "exists",
}


class FileTool:
    name = "file_tool"
    tool_type = "file_tool"

    supported_intents: set[str] = {
        "write_file", "append_file", "read_file", "delete_file",
        "move_file", "list_dir", "create_dir", "delete_dir",
    }

    def can_handle(self, intent: str) -> bool:
        return intent in self.supported_intents

    async def run(self, action: str, args: dict):
        # Normalise action string via alias map
        resolved_action = _ACTION_ALIASES.get(action, action)

        # FIX: path resolution — trust the fully-built "path" key from
        # file_ops._resolve_path / planner._resolve_args_for_intent.
        # Previously, file_tool required "path" to exist and then the
        # executor would call create_dir with raw args (dir_name + location)
        # causing a second dir_name append → ali/ali double-nesting.
        #
        # Now: if "path" is missing, build it once here from cwd+dir_name
        # so there is exactly one place that constructs the final path.
        path_str = args.get("path")
        if not path_str:
            if resolved_action == "mkdir" and args.get("dir_name"):
                base = args.get("cwd_resolved") or args.get("cwd") or os.getcwd()
                path_str = str(Path(base) / args["dir_name"])
            else:
                return False, "No path provided."

        safe_path = Path(path_str).resolve()
        await bus.emit(
            "file_op_started",
            {"action": resolved_action, "path": str(safe_path)},
            source="file_tool",
        )

        try:
            if resolved_action == "write":
                return await asyncio.to_thread(self._write_file, safe_path, args.get("data", ""))
            elif resolved_action == "append":
                return await asyncio.to_thread(self._append_file, safe_path, args.get("data", ""))
            elif resolved_action == "mkdir":
                return await asyncio.to_thread(self._mkdir, safe_path, args.get("exist_ok", True))
            elif resolved_action == "rmdir":
                return await asyncio.to_thread(self._delete_item, safe_path)
            elif resolved_action == "read":
                return await asyncio.to_thread(self._read_file, safe_path)
            elif resolved_action == "delete":
                return await asyncio.to_thread(self._delete_item, safe_path)
            elif resolved_action == "list":
                return await asyncio.to_thread(self._list_directory, safe_path)
            elif resolved_action == "exists":
                return await asyncio.to_thread(self._check_exists, safe_path)
            elif resolved_action == "move":
                return await asyncio.to_thread(self._move_item, safe_path, args.get("destination"))
            return False, f"Unknown action: {action!r}"
        except Exception as exc:
            logger.error("FileTool error (action=%s path=%s): %s", action, safe_path, exc)
            return False, f"File Error: {exc}"
 
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
 
 
file_tool = FileTool()