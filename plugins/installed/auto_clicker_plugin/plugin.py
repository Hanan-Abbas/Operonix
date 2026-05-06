from __future__ import annotations
import sys as _sys, os as _os
_plugin_dir = _os.path.abspath(_os.path.dirname(__file__))
_project_root = _os.path.dirname(
    _os.path.dirname(_os.path.dirname(_plugin_dir)))
if _project_root not in _sys.path:
    _sys.path.insert(0, _project_root)

"""

Auto-generated plugin: auto_clicker_plugin

Intent: auto clicker

Description: Auto-generated plugin to handle: auto clicker

Version: 1.0

Generated: 2026-05-06 07:58 UTC



IMPORTANT: Access system services ONLY via the capability registry:

    service = registry.get("vision_service")

    if not service or not service.is_available():

        return {"status": "error", "message": "Service unavailable"}

"""

from plugins.manifest_schema import BasePlugin

import asyncio

from capabilities.registry import capability_registry

import keyboard



class AutoClickerPlugin(BasePlugin):

    """

    Auto-generated plugin to handle: auto clicker

    Handles intent: auto clicker

    """

    name        = "auto_clicker_plugin"

    description = "Auto-generated plugin to handle: auto clicker"

    version     = "1.0"

    permissions = ["ui_interaction", "screen_read"]

    safe_mode   = True

    allowed_services = ["vision_service", "automation_service"]



    def validate(self, args: dict) -> str | None:

        # No required args for this intent

        return None



    async def run(self, context: dict, args: dict) -> dict:

        try:

            # Access automation via capability registry ONLY

            automation = capability_registry.get("automation_service")

            if automation is None:

                return {"status": "error", "message": "automation_service not available"}



            # Start auto clicker

            def auto_click():

                while True:

                    if keyboard.is_pressed('alt+s'):

                        break

                    keyboard.press_and_release('left')

                    asyncio.sleep(0.1)



            import threading

            threading.Thread(target=auto_click).start()



            return {"status": "success", "result": None, "intent": "auto clicker"}



        except Exception as e:

            return {"status": "error", "message": str(e), "intent": "auto clicker"}