from __future__ import annotations
import sys as _sys, os as _os
_plugin_dir = _os.path.abspath(_os.path.dirname(__file__))
_project_root = _os.path.dirname(
    _os.path.dirname(_os.path.dirname(_plugin_dir)))
if _project_root not in _sys.path:
    _sys.path.insert(0, _project_root)

"""
Plugin: app_opening_plugin
Intent: app opening
Category: automation
Description: Auto-generated plugin to handle: app opening
Version: 1.0
Generated: 2026-05-12 09:12 UTC
"""

# Standard library imports — always available
import time
import threading
import os
import sys
import subprocess

# NOTE: from __future__ imports and sys.path bootstrap are injected
# automatically by sandbox_runner — do NOT add them here.

from plugins.manifest_schema import BasePlugin

class AppOpeningPlugin(BasePlugin):
    """
    Auto-generated plugin to handle: app opening
    Category: automation (one-shot UI interaction)

    Pattern: performs a UI action and returns the result.
    Uses pyautogui and keyboard directly — these are standalone libraries,
    not registry services. Do NOT use capability_registry for UI actions.
    """
    name             = "app_opening_plugin"
    description      = "Auto-generated plugin to handle: app opening"
    version          = "1.0"
    permissions      = ["ui_interaction", "screen_read"]
    safe_mode        = True
    allowed_services = []

    def validate(self, args: dict) -> str | None:
        # TODO: Add validation for any args your action requires.
        # Example: if not args.get("target"): return "Missing 'target'"
        if not args.get("app_name"):
            return "Missing 'app_name'"
        return None

    async def run(self, context: dict, args: dict) -> dict:
        try:
            import shutil

            app_name = str(args.get("app_name", "")).strip()

            # ── Common app name aliases (user-friendly → binary name) ─────────
            _ALIASES = {
                "chrome":        "google-chrome",
                "google chrome": "google-chrome",
                "firefox":       "firefox",
                "vscode":        "code",
                "vs code":       "code",
                "visual studio code": "code",
                "cursor":        "cursor",
                "terminal":      "gnome-terminal",
                "files":         "nautilus",
                "calculator":    "gnome-calculator",
                "text editor":   "gedit",
            }
            binary = _ALIASES.get(app_name.lower(), app_name)

            # ── Strategy 1: run binary directly if found on PATH ──────────────
            if shutil.which(binary):
                subprocess.Popen(
                    [binary],
                    stdout=subprocess.DEVNULL,
                    stderr=subprocess.DEVNULL,
                    start_new_session=True,   # detach from parent process
                )
                return {
                    "status":  "success",
                    "result":  f"Launched '{binary}'",
                    "intent":  "app opening",
                    "app":     binary,
                }

            # ── Strategy 2: try common Linux launchers ────────────────────────
            for launcher in ("gtk-launch", "gio", "xdg-open"):
                if shutil.which(launcher):
                    result = subprocess.run(
                        [launcher, binary],
                        capture_output=True, text=True, timeout=10,
                    )
                    if result.returncode == 0:
                        return {
                            "status": "success",
                            "result": f"Opened '{binary}' via {launcher}",
                            "intent": "app opening",
                        }

            return {
                "status":  "error",
                "message": f"Could not find or launch '{app_name}' (tried binary='{binary}'). "
                           f"Is it installed and on PATH?",
                "intent":  "app opening",
            }

        except Exception as e:
            return {"status": "error", "message": str(e), "intent": "app opening"}