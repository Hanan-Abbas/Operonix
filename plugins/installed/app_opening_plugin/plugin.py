from __future__ import annotations
import sys as _sys, os as _os
_plugin_dir = _os.path.abspath(_os.path.dirname(__file__))
_project_root = _os.path.dirname(
    _os.path.dirname(_os.path.dirname(_plugin_dir)))
if _project_root not in _sys.path:
    _sys.path.insert(0, _project_root)

# Auto-generated plugin: app_opening_plugin
# Intent: app opening
# Description: Auto-generated plugin to handle: app opening
# Version: 1.0
# Generated: 2026-05-05 16:35 UTC

from plugins.manifest_schema import BasePlugin
import asyncio
from capabilities.registry import capability_registry

class AppOpeningPlugin(BasePlugin):
    """Auto-generated plugin to handle: app opening"""
    name        = "app_opening_plugin"
    description = "Auto-generated plugin to handle: app opening"
    version     = "1.0"
    permissions = ["ui_interaction", "screen_read"]
    safe_mode   = True
    allowed_services = ["vision_service", "automation_service"]

    def validate(self, args: dict) -> str | None:
        if 'app_name' not in args:
            return 'Missing required argument: app_name'
        return None

    async def run(self, context: dict, args: dict) -> dict:
        try:
            automation = capability_registry.get("automation_service")
            if automation is None:
                return {"status": "error", "message": "automation_service not available"}

            app_name = args.get('app_name')
            if not app_name:
                return {"status": "error", "message": "App name is required"}

            # Check if the app is installed
            installed_apps = await automation.get_installed_apps()
            if app_name not in installed_apps:
                return {"status": "error", "message": f"App {app_name} is not installed"}

            # Open the app
            await automation.open_app(app_name)

            return {"status": "success", "result": None, "intent": "app opening"}

        except Exception as e:
            return {"status": "error", "message": str(e), "intent": "app opening"}