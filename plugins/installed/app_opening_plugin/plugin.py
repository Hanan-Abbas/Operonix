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
            # ── Read args (with safe defaults) ────────────────────────────────
            app_name = str(args.get("app_name", ""))

            # ── Perform the action ────────────────────────────────────────────
            # Use shell command to open the app
            result = subprocess.run(
                ["xdg-open", app_name],                 # command as list (safe, no shell injection)
                capture_output=True,
                text=True,
                timeout=30,
            )
            stdout = result.stdout
            returncode = result.returncode

            if returncode == 0:
                return {"status": "success", "result": "App opened successfully", "intent": "app opening"}
            else:
                return {"status": "error", "message": "Failed to open app", "intent": "app opening"}

        except Exception as e:
            return {"status": "error", "message": str(e), "intent": "app opening"}