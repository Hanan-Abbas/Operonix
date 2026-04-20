"""
plugins/loader.py

Hot-reload plugin loader.

Responsibilities:
- Scan plugins/installed/ directory on startup
- Load each plugin's plugin.py using importlib (no restart required)
- Validate plugin class against BasePlugin interface
- Register loaded plugins into plugin_registry
- Support hot-reload (re-import without restarting the agent)
"""
from __future__ import annotations

import importlib.util
import logging
import os
from pathlib import Path
from typing import Type

from core.config import settings
from core.event_bus import bus
from plugins.manifest_schema import BasePlugin, PluginManifest, PluginStatus
from plugins.registry import PluginEntry, plugin_registry

logger = logging.getLogger("PluginLoader")

PLUGINS_INSTALLED_DIR = os.path.join(
    str(getattr(settings, "PLUGINS_DIR", "plugins")), "installed"
)


class PluginLoader:
    """
    Scans and loads plugins from the installed/ directory.

    Directory layout expected:
        plugins/installed/
            my_plugin/
                manifest.json
                plugin.py
                tests/
                    test_plugin.py
    """

    def __init__(self):
        self.logger = logging.getLogger("PluginLoader")
        self._installed_dir = PLUGINS_INSTALLED_DIR

    async def start(self):
        """Load all installed plugins on agent startup."""
        os.makedirs(self._installed_dir, exist_ok=True)
        loaded = await self.load_all()
        self.logger.info(
            f"🔌 Plugin Loader: {loaded} plugin(s) loaded from {self._installed_dir}"
        )

        # Listen for hot-reload requests
        bus.subscribe("plugin_reload_requested", self._on_reload_requested)

    async def load_all(self) -> int:
        """Scan installed/ and load every valid plugin directory."""
        count = 0
        if not os.path.isdir(self._installed_dir):
            return 0

        for entry_name in os.listdir(self._installed_dir):
            plugin_dir = os.path.join(self._installed_dir, entry_name)
            if not os.path.isdir(plugin_dir):
                continue
            success = await self.load_plugin(plugin_dir)
            if success:
                count += 1
        return count

    async def load_plugin(self, plugin_dir: str) -> bool:
        """
        Load a single plugin from its directory.
        Returns True if successfully loaded and registered.
        """
        plugin_file = os.path.join(plugin_dir, "plugin.py")
        manifest_file = os.path.join(plugin_dir, "manifest.json")

        # Both files must exist
        if not os.path.exists(plugin_file):
            self.logger.warning(f"Missing plugin.py in {plugin_dir}")
            return False
        if not os.path.exists(manifest_file):
            self.logger.warning(f"Missing manifest.json in {plugin_dir}")
            return False

        # Load manifest
        manifest = PluginManifest.load(plugin_dir)
        if not manifest:
            self.logger.warning(f"Failed to parse manifest.json in {plugin_dir}")
            return False

        # Skip untrusted or retired plugins at load time
        if manifest.status in (PluginStatus.UNTRUSTED, PluginStatus.RETIRED):
            self.logger.info(
                f"⏭️  Skipping plugin '{manifest.name}' (status={manifest.status.value})"
            )
            return False

        # Import the module
        plugin_class = self._import_plugin_class(plugin_file, manifest.name)
        if plugin_class is None:
            return False

        # Instantiate
        try:
            instance = plugin_class()
        except Exception as e:
            self.logger.error(
                f"Failed to instantiate plugin '{manifest.name}': {e}"
            )
            return False

        entry = PluginEntry(
            manifest=manifest,
            instance=instance,
            plugin_dir=plugin_dir,
        )
        plugin_registry.register(entry)
        self.logger.info(
            f"✅ Loaded plugin: '{manifest.name}' v{manifest.version}"
        )
        return True

    def _import_plugin_class(
        self, plugin_file: str, plugin_name: str
    ) -> Type[BasePlugin] | None:
        """
        Dynamically import a plugin.py and extract the BasePlugin subclass.
        Uses importlib to allow hot-reload without module cache conflicts.
        """
        try:
            # Use a unique module name per plugin to avoid cache collisions
            module_name = f"plugins.installed.{plugin_name}_module"
            spec = importlib.util.spec_from_file_location(module_name, plugin_file)
            module = importlib.util.module_from_spec(spec)
            spec.loader.exec_module(module)

            # Find the first BasePlugin subclass defined in the module
            for attr_name in dir(module):
                attr = getattr(module, attr_name)
                if (
                    isinstance(attr, type)
                    and issubclass(attr, BasePlugin)
                    and attr is not BasePlugin
                ):
                    self.logger.debug(
                        f"Found plugin class '{attr_name}' in {plugin_file}"
                    )
                    return attr

            self.logger.error(
                f"No BasePlugin subclass found in {plugin_file}"
            )
            return None

        except Exception as e:
            self.logger.error(
                f"Failed to import plugin '{plugin_name}' from {plugin_file}: {e}",
                exc_info=True,
            )
            return None

    async def hot_reload(self, plugin_name: str) -> bool:
        """
        Hot-reload a specific plugin by name without restarting the agent.
        Finds the plugin directory, unregisters the old instance, loads fresh.
        """
        plugin_dir = os.path.join(self._installed_dir, plugin_name)
        if not os.path.isdir(plugin_dir):
            self.logger.error(f"Plugin directory not found for hot-reload: {plugin_dir}")
            return False

        # Unregister old instance
        plugin_registry.unregister(plugin_name)

        # Reload fresh
        success = await self.load_plugin(plugin_dir)
        if success:
            self.logger.info(f"🔄 Hot-reloaded plugin: '{plugin_name}'")
            bus.publish(
                "plugin_hot_reloaded",
                {"name": plugin_name},
                source="plugin_loader",
            )
        return success

    async def _on_reload_requested(self, event):
        """Event bus handler for hot-reload requests."""
        name = event.data.get("name")
        if name:
            await self.hot_reload(name)


# Global instance
plugin_loader = PluginLoader()