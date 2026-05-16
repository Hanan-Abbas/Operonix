from __future__ import annotations
import sys as _sys, os as _os
_plugin_dir = _os.path.abspath(_os.path.dirname(__file__))
_project_root = _os.path.dirname(
    _os.path.dirname(_os.path.dirname(_plugin_dir)))
if _project_root not in _sys.path:
    _sys.path.insert(0, _project_root)

"""
Plugin: folder_organizer_plugin
Intent: folder_organizer
Category: file
Description: Auto-generated plugin to handle: folder_organizer
Version: 1.0
Generated: 2026-05-16 08:25 UTC
"""

# Standard library imports — always available
import time
import threading
import os
import sys
import shutil

# NOTE: from __future__ imports and sys.path bootstrap are injected
# automatically by sandbox_runner — do NOT add them here.

from plugins.manifest_schema import BasePlugin

class FolderOrganizerPlugin(BasePlugin):
    """
    Auto-generated plugin to handle: folder_organizer
    Category: file (filesystem operations)

    Pattern: uses os, shutil, pathlib (stdlib) directly.
    All paths must stay within safe directories.
    """
    name             = "folder_organizer_plugin"
    description      = "Auto-generated plugin to handle: folder_organizer"
    version          = "1.0"
    permissions      = ["file_read", "file_write"]
    safe_mode        = True
    allowed_services = []

    # Restrict operations to safe paths (never touch system files)
    _SAFE_ROOT = os.path.expanduser("~")

    def validate(self, args: dict) -> str | None:
        path = args.get("path", "")
        if not path:
            return "Missing required argument: 'path'"
        # Safety: path must be within home directory
        abs_path = os.path.realpath(os.path.expanduser(str(path)))
        if not abs_path.startswith(self._SAFE_ROOT):
            return f"Path outside safe root ({self._SAFE_ROOT}): {path}"
        return None

    async def run(self, context: dict, args: dict) -> dict:
        error = self.validate(args)
        if error:
            return {"status": "error", "message": error, "intent": "folder_organizer"}
        try:
            import shutil
            path = os.path.realpath(os.path.expanduser(str(args.get("path", ""))))

            # Create directories for each file type
            image_dir = os.path.join(path, "images")
            docs_dir = os.path.join(path, "docs")
            videos_dir = os.path.join(path, "videos")
            archives_dir = os.path.join(path, "archives")

            os.makedirs(image_dir, exist_ok=True)
            os.makedirs(docs_dir, exist_ok=True)
            os.makedirs(videos_dir, exist_ok=True)
            os.makedirs(archives_dir, exist_ok=True)

            # Move files to their respective directories
            for filename in os.listdir(path):
                if filename == "images" or filename == "docs" or filename == "videos" or filename == "archives":
                    continue
                file_path = os.path.join(path, filename)
                if os.path.isfile(file_path):
                    if filename.endswith(('.jpg', '.png', '.gif', '.bmp', '.tiff')):
                        shutil.move(file_path, image_dir)
                    elif filename.endswith(('.doc', '.docx', '.pdf', '.txt', '.odt')):
                        shutil.move(file_path, docs_dir)
                    elif filename.endswith(('.mp4', '.avi', '.mov', '.flv', '.wmv')):
                        shutil.move(file_path, videos_dir)
                    elif filename.endswith(('.zip', '.rar', '.7z', '.tar', '.gz', '.bz2')):
                        shutil.move(file_path, archives_dir)

            return {"status": "success", "result": "Files organized successfully", "intent": "folder_organizer"}

        except Exception as e:
            return {"status": "error", "message": str(e), "intent": "folder_organizer"}