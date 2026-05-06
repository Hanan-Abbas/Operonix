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

            plugin_file   = os.path.join(plugin_dir, "plugin.py")
            manifest_file = os.path.join(plugin_dir, "manifest.json")

            # Silently skip directories that are clearly incomplete —
            # these are leftovers from failed/in-progress generation runs.
            # A valid plugin always has BOTH plugin.py AND manifest.json.
            if not os.path.exists(manifest_file) and not os.path.exists(plugin_file):
                self.logger.debug(
                    f"Skipping empty directory (no plugin files): {plugin_dir}"
                )
                continue

            if not os.path.exists(manifest_file):
                # Has plugin.py but no manifest.json.
                # This is a real broken install (plugin.py exists but manifest
                # was never written) — warn so the developer knows about it.
                # Contrast with fully-empty dirs (no plugin.py either) which
                # are silent generation artefacts handled above.
                self.logger.warning(
                    f"⚠️  Plugin dir '{os.path.basename(plugin_dir)}' has plugin.py "
                    f"but no manifest.json — skipping. "
                    f"If this is a failed generation artefact, delete the directory: "
                    f"{plugin_dir}"
                )
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
            self.logger.debug(f"Skipping {plugin_dir}: no plugin.py found.")
            return False
        if not os.path.exists(manifest_file):
            # load_all() already guards this — but if load_plugin() is called
            # directly (e.g. hot_reload) and the manifest is missing, log at
            # debug not warning since it is an expected transient state during
            # generation retries.
            self.logger.debug(
                f"Skipping {plugin_dir}: no manifest.json — "
                f"plugin is pending or generation failed."
            )
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
        name = event.data.get("name") if hasattr(event, "data") and isinstance(event.data, dict) else None
        if name:
            await self.hot_reload(name)

    # ── API-facing methods ────────────────────────────────────────────────────

    def list_plugins(self) -> list[dict]:
        """
        Return a serialisable list of all registered plugins and their status.
        Called by GET /api/plugins.
        """
        result = []
        for name, entry in plugin_registry.entries.items():
            m = entry.manifest
            result.append({
                "name":         m.name,
                "version":      m.version,
                "description":  getattr(m, "description", ""),
                "status":       m.status.value if hasattr(m.status, "value") else str(m.status),
                "enabled":      m.status.value not in ("untrusted", "retired", "disabled")
                                if hasattr(m.status, "value") else True,
                "loaded":       True,
                "capabilities": getattr(m, "capabilities", []),
                "plugin_dir":   entry.plugin_dir,
            })
        return result

    def get_plugin(self, name: str) -> dict | None:
        """Return manifest dict for a single plugin, or None if not found."""
        entry = plugin_registry.entries.get(name)
        if entry is None:
            return None
        m = entry.manifest
        return {
            "name":         m.name,
            "version":      m.version,
            "description":  getattr(m, "description", ""),
            "status":       m.status.value if hasattr(m.status, "value") else str(m.status),
            "enabled":      m.status.value not in ("untrusted", "retired", "disabled")
                            if hasattr(m.status, "value") else True,
            "loaded":       True,
            "capabilities": getattr(m, "capabilities", []),
            "plugin_dir":   entry.plugin_dir,
        }

    async def enable(self, name: str) -> str:
        """Enable a registered plugin (set status to active)."""
        entry = plugin_registry.entries.get(name)
        if entry is None:
            raise FileNotFoundError(f"Plugin '{name}' not found.")
        entry.manifest.status = PluginStatus.ACTIVE
        self.logger.info("Plugin '%s' enabled.", name)
        return f"Plugin '{name}' enabled."

    async def disable(self, name: str) -> str:
        """Disable a registered plugin without removing it."""
        entry = plugin_registry.entries.get(name)
        if entry is None:
            raise FileNotFoundError(f"Plugin '{name}' not found.")
        entry.manifest.status = PluginStatus.RETIRED
        self.logger.info("Plugin '%s' disabled.", name)
        return f"Plugin '{name}' disabled."

    async def reload(self, name: str) -> str:
        """Hot-reload a single plugin by name."""
        success = await self.hot_reload(name)
        if not success:
            raise FileNotFoundError(f"Plugin '{name}' could not be reloaded.")
        return f"Plugin '{name}' reloaded."

    async def reload_all(self) -> dict:
        """Hot-reload all currently registered plugins."""
        results = {}
        for name in list(plugin_registry.entries.keys()):
            try:
                ok = await self.hot_reload(name)
                results[name] = "reloaded" if ok else "failed"
            except Exception as exc:  # noqa: BLE001
                results[name] = f"error: {exc}"
        return results

    async def remove(self, name: str) -> str:
        """Unload a plugin and delete its directory from disk."""
        import shutil
        entry = plugin_registry.entries.get(name)
        if entry is None:
            raise FileNotFoundError(f"Plugin '{name}' not found.")
        plugin_dir = entry.plugin_dir
        plugin_registry.unregister(name)
        if os.path.isdir(plugin_dir):
            shutil.rmtree(plugin_dir, ignore_errors=True)
            self.logger.info("Plugin '%s' removed from disk (%s).", name, plugin_dir)
        return f"Plugin '{name}' removed."

    def find(self, app: str = "", intent: str = "") -> list[dict]:
        """
        Return plugins whose capabilities loosely match the given intent/app.
        Used by PanelController's plugin_registry callable.
        """
        matches = []
        for entry in plugin_registry.entries.values():
            caps = getattr(entry.manifest, "capabilities", [])
            if not intent or any(intent.lower() in str(c).lower() for c in caps):
                m = entry.manifest
                matches.append({
                    "name":        m.name,
                    "description": getattr(m, "description", ""),
                    "capabilities": caps,
                })
        return matches


# Global instance
plugin_loader = PluginLoader()