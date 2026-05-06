"""
plugins/template_engine.py

Generates plugin and test file skeletons based on intent category.
Provides structured scaffolding to the generator so the LLM only
needs to fill in the logic, not the boilerplate.

Templates cover:
  - automation  (screen reader, vision, UI interaction)
  - web         (web search, URL operations)
  - file        (file system operations)
  - command     (shell command execution via registry)
  - generic     (fallback for unknown categories)
"""
from __future__ import annotations

import logging
import re

logger = logging.getLogger("TemplateEngine")

# ── Category Detection ─────────────────────────────────────────────────────────

_CATEGORY_KEYWORDS = {
    "automation": [
        "click", "type", "screen", "window", "ui", "button", "input",
        "keyboard", "mouse", "drag", "scroll", "focus", "app", "launch",
        "open", "close", "vision", "ocr", "screenshot",
    ],
    "web": [
        "search", "url", "browse", "website", "http", "download",
        "fetch", "scrape", "web",
    ],
    "file": [
        "file", "folder", "directory", "read", "write", "delete",
        "move", "copy", "rename", "path", "save",
    ],
    "command": [
        "run", "execute", "shell", "command", "terminal", "process",
        "script", "bash", "cmd",
    ],
}


def detect_category(intent: str, description: str = "") -> str:
    """Detect the most likely plugin category from intent and description."""
    combined = f"{intent} {description}".lower().replace("_", " ")
    scores = {cat: 0 for cat in _CATEGORY_KEYWORDS}
    for cat, keywords in _CATEGORY_KEYWORDS.items():
        for kw in keywords:
            if kw in combined:
                scores[cat] += 1
    best = max(scores, key=scores.get)
    return best if scores[best] > 0 else "generic"


# ── Plugin Code Templates ──────────────────────────────────────────────────────

_PLUGIN_HEADER = '''"""
Auto-generated plugin: {plugin_name}
Intent: {intent}
Description: {description}
Version: {version}
Generated: {timestamp}

IMPORTANT: Access system services ONLY via the capability registry:
    service = capability_registry.get("vision_service")
    if service is None:
        return {{"status": "error", "message": "Service unavailable"}}
    NOTE: capability_registry.get() returns the service or None — no is_available() method exists.
"""
# NOTE: `from __future__ import annotations` is injected at the top of the
# final file by sandbox_runner._patch_plugin_sys_path — do NOT add it here.
from plugins.manifest_schema import BasePlugin


'''

_AUTOMATION_TEMPLATE = '''class {class_name}(BasePlugin):
    """
    {description}
    Handles intent: {intent}
    """
    name        = "{plugin_name}"
    description = "{description}"
    version     = "{version}"
    permissions = ["ui_interaction", "screen_read"]
    safe_mode   = True
    allowed_services = ["vision_service", "automation_service"]

    def validate(self, args: dict) -> str | None:
        # TODO: validate required args for this intent
        return None

    async def run(self, context: dict, args: dict) -> dict:
        try:
            # Access automation via capability registry ONLY
            from capabilities.registry import capability_registry

            automation = capability_registry.get("automation_service")
            if automation is None:
                return {{"status": "error", "message": "automation_service not available"}}

            # TODO: Implement the plugin logic for intent: {intent}
            # Use automation service methods to interact with the UI
            # Example: result = await automation(context, args)

            return {{"status": "success", "result": None, "intent": "{intent}"}}

        except Exception as e:
            return {{"status": "error", "message": str(e), "intent": "{intent}"}}
'''

_WEB_TEMPLATE = '''class {class_name}(BasePlugin):
    """
    {description}
    Handles intent: {intent}
    """
    name        = "{plugin_name}"
    description = "{description}"
    version     = "{version}"
    permissions = ["web_access"]
    safe_mode   = True
    allowed_services = ["web_service"]

    def validate(self, args: dict) -> str | None:
        # TODO: validate required args (e.g., url, query)
        return None

    async def run(self, context: dict, args: dict) -> dict:
        try:
            from capabilities.registry import capability_registry

            web = capability_registry.get("web_service")
            if web is None:
                return {{"status": "error", "message": "web_service not available"}}

            # TODO: Implement the plugin logic for intent: {intent}

            return {{"status": "success", "result": None, "intent": "{intent}"}}

        except Exception as e:
            return {{"status": "error", "message": str(e), "intent": "{intent}"}}
'''

_FILE_TEMPLATE = '''class {class_name}(BasePlugin):
    """
    {description}
    Handles intent: {intent}
    """
    name        = "{plugin_name}"
    description = "{description}"
    version     = "{version}"
    permissions = ["file_read", "file_write"]
    safe_mode   = True
    allowed_services = ["filesystem_service"]

    def validate(self, args: dict) -> str | None:
        if not args.get("path"):
            return "Missing required argument: 'path'"
        return None

    async def run(self, context: dict, args: dict) -> dict:
        try:
            from capabilities.registry import capability_registry

            fs = capability_registry.get("filesystem_service")
            if fs is None:
                return {{"status": "error", "message": "filesystem_service not available"}}

            # TODO: Implement the plugin logic for intent: {intent}

            return {{"status": "success", "result": None, "intent": "{intent}"}}

        except Exception as e:
            return {{"status": "error", "message": str(e), "intent": "{intent}"}}
'''

_COMMAND_TEMPLATE = '''class {class_name}(BasePlugin):
    """
    {description}
    Handles intent: {intent}
    """
    name        = "{plugin_name}"
    description = "{description}"
    version     = "{version}"
    permissions = ["command_execution"]
    safe_mode   = True
    allowed_services = ["command_service"]

    def validate(self, args: dict) -> str | None:
        if not args.get("command"):
            return "Missing required argument: 'command'"
        return None

    async def run(self, context: dict, args: dict) -> dict:
        try:
            from capabilities.registry import capability_registry

            cmd_service = capability_registry.get("command_service")
            if cmd_service is None:
                return {{"status": "error", "message": "command_service not available"}}

            # TODO: Implement the plugin logic for intent: {intent}

            return {{"status": "success", "result": None, "intent": "{intent}"}}

        except Exception as e:
            return {{"status": "error", "message": str(e), "intent": "{intent}"}}
'''

_GENERIC_TEMPLATE = '''class {class_name}(BasePlugin):
    """
    {description}
    Handles intent: {intent}
    """
    name        = "{plugin_name}"
    description = "{description}"
    version     = "{version}"
    permissions = []
    safe_mode   = True
    allowed_services = []

    def validate(self, args: dict) -> str | None:
        # TODO: add validation for required args
        return None

    async def run(self, context: dict, args: dict) -> dict:
        try:
            from capabilities.registry import capability_registry

            # TODO: Implement the plugin logic for intent: {intent}
            # Access ALL services via capability_registry.get("service_name")

            return {{"status": "success", "result": None, "intent": "{intent}"}}

        except Exception as e:
            return {{"status": "error", "message": str(e), "intent": "{intent}"}}
'''

# ── Test Template ──────────────────────────────────────────────────────────────

_TEST_TEMPLATE = '''"""
Auto-generated tests for plugin: {plugin_name}
Intent: {intent}
"""
import asyncio
import pytest
import sys
import os

# Add plugin directory to path for import
sys.path.insert(0, os.path.dirname(os.path.dirname(os.path.abspath(__file__))))

from plugin import {class_name}


@pytest.fixture
def plugin():
    return {class_name}()


@pytest.fixture
def base_context():
    return {{"active_window": "test", "app_type": "test"}}


def test_plugin_has_required_attributes(plugin):
    assert hasattr(plugin, "name") and plugin.name
    assert hasattr(plugin, "description") and plugin.description
    assert hasattr(plugin, "version") and plugin.version
    assert hasattr(plugin, "run")
    assert hasattr(plugin, "validate")
    assert asyncio.iscoroutinefunction(plugin.run)


def test_validate_returns_none_for_valid_args(plugin):
    # TODO: Replace with valid args for intent: {intent}
    result = plugin.validate({{}})
    assert result is None or isinstance(result, str)


def test_run_returns_dict_with_status(plugin, base_context):
    result = asyncio.run(plugin.run(base_context, {{}}))
    assert isinstance(result, dict), "run() must return a dict"
    assert "status" in result, "result must have a 'status' key"
    assert result["status"] in ("success", "error"), (
        f"status must be 'success' or 'error', got: {{result['status']}}"
    )


def test_run_handles_empty_args_gracefully(plugin, base_context):
    """Plugin must not crash on empty args — return error dict instead."""
    try:
        result = asyncio.run(plugin.run(base_context, {{}}))
        assert isinstance(result, dict)
        assert "status" in result
    except Exception as exc:
        pytest.fail(f"run() raised an exception instead of returning error dict: {{exc}}")


def test_run_handles_none_context(plugin):
    """Plugin must handle None or empty context gracefully."""
    try:
        result = asyncio.run(plugin.run({{}}, {{}}))
        assert isinstance(result, dict)
    except Exception as exc:
        pytest.fail(f"run() crashed on empty context: {{exc}}")
'''

_CATEGORY_TEMPLATES = {
    "automation": _AUTOMATION_TEMPLATE,
    "web":        _WEB_TEMPLATE,
    "file":       _FILE_TEMPLATE,
    "command":    _COMMAND_TEMPLATE,
    "generic":    _GENERIC_TEMPLATE,
}


class TemplateEngine:
    """
    Generates plugin and test scaffolding for a given intent/category.

    The generator calls get_plugin_skeleton() and get_test_skeleton()
    to get the boilerplate, then asks the LLM to fill in only the logic.
    """

    def __init__(self):
        self.logger = logging.getLogger("TemplateEngine")

    def get_plugin_skeleton(
        self,
        plugin_name: str,
        intent: str,
        description: str,
        version: str = "1.0",
        category: str | None = None,
    ) -> str:
        """
        Returns a full plugin.py skeleton string for the given intent.

        Args:
            plugin_name:  snake_case plugin name
            intent:       the capability gap intent
            description:  short description of what the plugin does
            version:      version string
            category:     override auto-detection (automation/web/file/command/generic)
        """
        from datetime import datetime

        if category is None:
            category = detect_category(intent, description)

        class_name = self._to_class_name(plugin_name)
        template = _CATEGORY_TEMPLATES.get(category, _GENERIC_TEMPLATE)

        header = _PLUGIN_HEADER.format(
            plugin_name=plugin_name,
            intent=intent,
            description=description,
            version=version,
            timestamp=datetime.utcnow().strftime("%Y-%m-%d %H:%M UTC"),
        )

        body = template.format(
            class_name=class_name,
            plugin_name=plugin_name,
            intent=intent,
            description=description,
            version=version,
        )

        self.logger.debug(
            f"Generated '{category}' skeleton for plugin '{plugin_name}'"
        )
        return header + body

    def get_test_skeleton(self, plugin_name: str, intent: str) -> str:
        """Returns a test_plugin.py skeleton for the given plugin."""
        class_name = self._to_class_name(plugin_name)
        return _TEST_TEMPLATE.format(
            plugin_name=plugin_name,
            intent=intent,
            class_name=class_name,
        )

    def get_category(self, intent: str, description: str = "") -> str:
        """Public helper to inspect which category would be selected."""
        return detect_category(intent, description)

    @staticmethod
    def _to_class_name(plugin_name: str) -> str:
        """Converts snake_case plugin_name to PascalCase class name."""
        return "".join(part.capitalize() for part in plugin_name.split("_"))


# Global instance
template_engine = TemplateEngine()