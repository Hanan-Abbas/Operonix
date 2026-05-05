"""
Auto-generated plugin: app_opening_plugin
Intent: app opening
Description: Auto-generated plugin to handle: app opening
Version: 1.0
Generated: 2026-05-05 08:36 UTC

IMPORTANT: Access system services ONLY via the capability registry:
    service = registry.get("vision_service")
    if not service or not service.is_available():
        return {"status": "error", "message": "Service unavailable"}
"""
import sys as _sys, os as _os
_plugin_dir = _os.path.abspath(_os.path.dirname(__file__))
_project_root = _os.path.dirname(_os.path.dirname(_os.path.dirname(_plugin_dir)))
if _project_root not in _sys.path:
    _sys.path.insert(0, _project_root)


from __future__ import annotations
from plugins.manifest_schema import BasePlugin
import asyncio
from capabilities.registry import capability_registry


class AppOpeningPlugin(BasePlugin):
    """
    Auto-generated plugin to handle: app opening
    Handles intent: app opening
    """
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

            if 'app_name' not in args:
                return {"status": "error", "message": "Missing required argument: app_name"}

            app_name = args['app_name']
            result = await automation.open_app(app_name)
            if result:
                return {"status": "success", "result": f"App {app_name} opened successfully", "intent": "app opening"}
            else:
                return {"status": "error", "message": f"Failed to open app {app_name}", "intent": "app opening"}

        except Exception as e:
            return {"status": "error", "message": str(e), "intent": "app opening"}