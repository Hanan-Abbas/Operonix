"""Auto-generated plugin: auto_clicker_plugin
Intent: auto clicker
Description: Performs automated clicking at a specified interval
Version: 1.0
Generated: 2026-05-04 UTC
"""
from __future__ import annotations

# sys.path bootstrap — required for sandbox subprocess to find project modules
import sys as _sys
import os as _os
_plugin_dir    = _os.path.abspath(_os.path.dirname(__file__))
_project_root  = _os.path.dirname(_os.path.dirname(_os.path.dirname(_plugin_dir)))
if _project_root not in _sys.path:
    _sys.path.insert(0, _project_root)

import asyncio
from plugins.manifest_schema import BasePlugin


class AutoClickerPlugin(BasePlugin):
    """Auto-generated plugin to handle: auto clicker
    Handles intent: auto clicker
    Performs automated mouse clicking at configurable intervals.
    """
    name             = "auto_clicker_plugin"
    description      = "Performs automated clicking at a specified interval and count"
    version          = "1.0"
    permissions      = ["ui_interaction", "screen_read"]
    safe_mode        = True
    allowed_services = ["automation_service"]

    def validate(self, args: dict) -> str | None:
        if "click_interval" not in args or "click_count" not in args:
            return "Missing required arguments: click_interval and click_count"
        if not isinstance(args["click_interval"], (int, float)):
            return "click_interval must be a number (seconds between clicks)"
        if not isinstance(args["click_count"], int) or args["click_count"] < 1:
            return "click_count must be a positive integer"
        return None

    async def run(self, context: dict, args: dict) -> dict:
        try:
            validation_error = self.validate(args)
            if validation_error:
                return {"status": "error", "message": validation_error, "intent": "auto clicker"}

            click_interval = float(args["click_interval"])
            click_count    = int(args["click_count"])

            # Access automation via capability registry ONLY
            try:
                from capabilities.registry import capability_registry
                automation = capability_registry.get("automation_service")
            except Exception:
                automation = None

            if automation is None:
                # Fallback: use xdotool via shell if automation_service unavailable
                import subprocess
                for i in range(click_count):
                    result = subprocess.run(
                        ["xdotool", "click", "1"],
                        capture_output=True, text=True, timeout=5
                    )
                    if result.returncode != 0:
                        return {
                            "status":  "error",
                            "message": f"xdotool click failed: {result.stderr}",
                            "intent":  "auto clicker",
                        }
                    if i < click_count - 1:
                        await asyncio.sleep(click_interval)

                return {
                    "status":  "success",
                    "result":  f"Clicked {click_count} time(s) at {click_interval}s interval",
                    "intent":  "auto clicker",
                }

            # Use automation_service if available
            for i in range(click_count):
                await automation.click()
                if i < click_count - 1:
                    await asyncio.sleep(click_interval)

            return {
                "status":  "success",
                "result":  f"Clicked {click_count} time(s) at {click_interval}s interval",
                "intent":  "auto clicker",
            }

        except Exception as exc:
            return {"status": "error", "message": str(exc), "intent": "auto clicker"}