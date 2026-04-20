"""
plugins/registry.py

Single source of truth for all installed plugins and their runtime state.

Responsibilities:
- Track all loaded plugin instances and their manifests
- Provide query interface (by name, by intent, by status)
- Register plugins into capability_registry so the brain can use them
- Publish registry change events to the bus
- Thread-safe operations
"""
from __future__ import annotations

import logging
import threading
from datetime import datetime
from typing import Any

from core.event_bus import bus
from plugins.manifest_schema import PluginManifest, PluginStatus, BasePlugin

logger = logging.getLogger("PluginRegistry")


class PluginEntry:
    """Container for a loaded plugin with its manifest and instance."""

    def __init__(
        self,
        manifest: PluginManifest,
        instance: BasePlugin | None = None,
        plugin_dir: str = "",
    ):
        self.manifest = manifest
        self.instance = instance
        self.plugin_dir = plugin_dir
        self.loaded_at = datetime.utcnow().isoformat()

    @property
    def name(self) -> str:
        return self.manifest.name

    @property
    def status(self) -> PluginStatus:
        return self.manifest.status

    @property
    def is_active(self) -> bool:
        return self.manifest.status == PluginStatus.TRUSTED
  
    def to_dict(self) -> dict:
        return {
            "name": self.name,
            "status": self.manifest.status.value,
            "intent": self.manifest.intent,
            "version": self.manifest.version,
            "trusted": self.manifest.trusted,
            "risk_level": self.manifest.risk_level.value,
            "success_rate": self.manifest.success_rate,
            "total_runs": self.manifest.total_runs,
            "loaded_at": self.loaded_at,
        }


class PluginRegistry:
    """
    Central registry for all installed plugins.

    Access pattern:
        plugin_registry.register(entry)
        plugin_registry.get("plugin_name")
        plugin_registry.get_by_intent("search_web")
        plugin_registry.list_trusted()
    """

    def __init__(self):
        self._entries: dict[str, PluginEntry] = {}
        self._lock = threading.RLock()

    @property
    def entries(self) -> dict[str, "PluginEntry"]:
        """Public read access to the entries dict (thread-safe snapshot)."""
        with self._lock:
            return dict(self._entries)
            
    # ── Registration ──────────────────────────────────────────────────────────

    def register(self, entry: PluginEntry) -> bool:
        """
        Register a plugin entry.
        If a plugin with the same name exists, it is replaced (hot-reload).
        Also registers the plugin as a capability in capability_registry.
        """
        with self._lock:
            existing = self._entries.get(entry.name)
            if existing:
                logger.info(
                    f"🔄 Hot-reloading plugin '{entry.name}' "
                    f"(v{existing.manifest.version} → v{entry.manifest.version})"
                )

            self._entries[entry.name] = entry
            logger.info(
                f"✅ Plugin registered: '{entry.name}' "
                f"status={entry.manifest.status.value}"
            )

        # Only register active/trusted plugins into capability_registry
        if entry.is_active and entry.instance:
            self._register_as_capability(entry)

        bus.publish(
            "plugin_registered",
            entry.to_dict(),
            source="plugin_registry",
        )
        return True

    def unregister(self, name: str) -> bool:
        """Remove a plugin from the registry and from capability_registry."""
        with self._lock:
            if name not in self._entries:
                return False
            del self._entries[name]

        # Remove from capability registry
        try:
            from capabilities.registry import capability_registry
            capability_registry.registry.pop(name, None)
            capability_registry.metadata.pop(name, None)
        except Exception as e:
            logger.warning(f"Could not remove '{name}' from capability_registry: {e}")

        bus.publish(
            "plugin_unregistered",
            {"name": name},
            source="plugin_registry",
        )
        logger.info(f"🗑️ Plugin unregistered: '{name}'")
        return True

    def _register_as_capability(self, entry: PluginEntry):
        """Injects a trusted plugin into the global capability_registry."""
        try:
            from capabilities.registry import capability_registry

            async def _capability_wrapper(context: dict, args: dict) -> dict:
                return await entry.instance.run(context, args)

            # Preserve async function identity
            _capability_wrapper.__name__ = entry.name

            capability_registry.register(
                entry.name,
                _capability_wrapper,
                metadata={
                    "description": entry.manifest.description,
                    "method": "plugin",
                    "tags": entry.manifest.tags,
                    "version": entry.manifest.version,
                    "intent": entry.manifest.intent,
                    "plugin": True,
                },
            )
            logger.info(f"🧩 Plugin '{entry.name}' injected into capability_registry.")
        except Exception as e:
            logger.error(f"Failed to inject plugin '{entry.name}' into capability_registry: {e}")

    # ── Query Interface ───────────────────────────────────────────────────────

    def get(self, name: str) -> PluginEntry | None:
        with self._lock:
            return self._entries.get(name)

    def get_by_intent(self, intent: str) -> list[PluginEntry]:
        """Find all plugins that handle a given intent."""
        with self._lock:
            return [
                e for e in self._entries.values()
                if e.manifest.intent == intent
            ]

    def list_all(self) -> list[PluginEntry]:
        with self._lock:
            return list(self._entries.values())

    def list_trusted(self) -> list[PluginEntry]:
        with self._lock:
            return [e for e in self._entries.values() if e.is_active]

    def list_by_status(self, status: PluginStatus) -> list[PluginEntry]:
        with self._lock:
            return [
                e for e in self._entries.values()
                if e.manifest.status == status
            ]

    def exists(self, name: str) -> bool:
        with self._lock:
            return name in self._entries

    # ── State Mutations ───────────────────────────────────────────────────────

    def update_status(self, name: str, status: PluginStatus, plugin_dir: str = "") -> bool:
        """Update a plugin's status and persist the manifest."""
        with self._lock:
            entry = self._entries.get(name)
            if not entry:
                return False
            entry.manifest.status = status
            entry.manifest.trusted = (status == PluginStatus.TRUSTED)
            entry.manifest.last_reviewed = datetime.utcnow().isoformat()

            if entry.plugin_dir or plugin_dir:
                entry.manifest.save(entry.plugin_dir or plugin_dir)

        # If now trusted, register as capability
        if status == PluginStatus.TRUSTED and entry.instance:
            self._register_as_capability(entry)
        elif status in (PluginStatus.RETIRED, PluginStatus.UNTRUSTED):
            self.unregister(name)

        bus.publish(
            "plugin_status_changed",
            {"name": name, "status": status.value},
            source="plugin_registry",
        )
        return True

    def revoke_trust(self, name: str, reason: str = "") -> bool:
        """Revoke trust for a plugin (e.g. repeated failures)."""
        logger.warning(f"🔒 Revoking trust for plugin '{name}': {reason}")
        bus.publish(
            "plugin_trust_revoked",
            {"name": name, "reason": reason},
            source="plugin_registry",
        )
        return self.update_status(name, PluginStatus.UNTRUSTED)

    def increment_stats(self, name: str, success: bool):
        """Update plugin run stats. Called by plugin_health_monitor."""
        with self._lock:
            entry = self._entries.get(name)
            if not entry:
                return
            entry.manifest.total_runs += 1
            if success:
                entry.manifest.total_successes += 1
            else:
                entry.manifest.total_failures += 1
            entry.manifest.last_run_at = datetime.utcnow().isoformat()
            if entry.plugin_dir:
                entry.manifest.save(entry.plugin_dir)

    # ── Summary ───────────────────────────────────────────────────────────────

    def summary(self) -> dict:
        with self._lock:
            entries = list(self._entries.values())
        return {
            "total": len(entries),
            "trusted": sum(1 for e in entries if e.is_active),
            "pending": sum(1 for e in entries if e.status == PluginStatus.PENDING),
            "untrusted": sum(1 for e in entries if e.status == PluginStatus.UNTRUSTED),
            "retired": sum(1 for e in entries if e.status == PluginStatus.RETIRED),
        }


# Global instance
plugin_registry = PluginRegistry()