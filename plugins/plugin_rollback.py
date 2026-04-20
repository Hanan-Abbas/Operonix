"""
plugins/plugin_rollback.py

Plugin-scoped rollback system.
Thin wrapper over debugging/rollback_manager with plugin-specific logic:
  - Backs up plugin.py and manifest.json together
  - Restores a prior version when evolution or fix fails
  - Tracks version history in manifest
  - Integrates with plugin_registry to update status on rollback
"""
from __future__ import annotations

import logging
import os
import shutil
from datetime import datetime
from pathlib import Path

from debugging.rollback_manager import rollback_manager
from plugins.manifest_schema import PluginManifest, PluginStatus

logger = logging.getLogger("PluginRollback")


class PluginRollbackManager:
    """
    Manages backup and restore of plugin files.

    Wraps the existing rollback_manager for plugin.py files and
    additionally handles manifest.json versioning.
    """

    def __init__(self):
        self.logger = logging.getLogger("PluginRollback")

    def create_snapshot(self, plugin_dir: str, plugin_name: str) -> dict:
        """
        Creates a timestamped backup of both plugin.py and manifest.json.

        Returns a snapshot dict:
        {
            "plugin_name": ...,
            "plugin_backup": "/path/to/plugin.py.bak_TIMESTAMP",
            "manifest_backup": "/path/to/manifest.json.bak_TIMESTAMP",
            "timestamp": "...",
        }
        """
        plugin_file   = os.path.join(plugin_dir, "plugin.py")
        manifest_file = os.path.join(plugin_dir, "manifest.json")

        snapshot = {
            "plugin_name": plugin_name,
            "plugin_backup": "",
            "manifest_backup": "",
            "timestamp": datetime.utcnow().isoformat(),
        }

        if os.path.exists(plugin_file):
            backup = rollback_manager.create_backup(plugin_file)
            snapshot["plugin_backup"] = backup
            self.logger.info(f"💾 Plugin backup: {backup}")

        if os.path.exists(manifest_file):
            backup = rollback_manager.create_backup(manifest_file)
            snapshot["manifest_backup"] = backup
            self.logger.info(f"💾 Manifest backup: {backup}")

        return snapshot

    def restore_snapshot(self, plugin_dir: str, snapshot: dict) -> bool:
        """
        Restores plugin.py and manifest.json from a snapshot.
        Returns True if both files were restored successfully.
        """
        plugin_name   = snapshot.get("plugin_name", "unknown")
        plugin_file   = os.path.join(plugin_dir, "plugin.py")
        manifest_file = os.path.join(plugin_dir, "manifest.json")

        plugin_ok   = True
        manifest_ok = True

        if snapshot.get("plugin_backup"):
            plugin_ok = rollback_manager.restore_backup(
                plugin_file, snapshot["plugin_backup"]
            )

        if snapshot.get("manifest_backup"):
            manifest_ok = rollback_manager.restore_backup(
                manifest_file, snapshot["manifest_backup"]
            )

        success = plugin_ok and manifest_ok

        if success:
            self.logger.info(f"♻️ Plugin '{plugin_name}' rolled back successfully.")
            self._update_status_after_rollback(plugin_dir, plugin_name)
        else:
            self.logger.error(f"❌ Rollback failed for plugin '{plugin_name}'.")

        return success

    def _update_status_after_rollback(self, plugin_dir: str, plugin_name: str):
        """
        After restoring files, update the plugin registry to reflect rollback.
        Marks the plugin as UNTRUSTED so it must be re-reviewed.
        """
        try:
            from plugins.registry import plugin_registry
            plugin_registry.update_status(
                plugin_name,
                PluginStatus.UNTRUSTED,
                plugin_dir=plugin_dir,
            )
        except Exception as e:
            self.logger.warning(
                f"Could not update registry status after rollback: {e}"
            )

    def record_version_in_manifest(
        self, plugin_dir: str, old_version: str, new_version: str, reason: str
    ):
        """
        Updates the manifest.json to track the old version in previous_versions
        before writing a new evolved version.
        """
        manifest = PluginManifest.load(plugin_dir)
        if not manifest:
            return

        if old_version and old_version not in manifest.previous_versions:
            manifest.previous_versions.append(old_version)

        manifest.changelog.append(
            f"[{datetime.utcnow().strftime('%Y-%m-%d')}] v{old_version} → v{new_version}: {reason}"
        )
        manifest.version = new_version
        manifest.save(plugin_dir)
        self.logger.info(
            f"📝 Version recorded: '{manifest.name}' {old_version} → {new_version}"
        )

    def delete_backups(self, snapshot: dict):
        """
        Cleanup backup files after a successful evolution or fix.
        Delegates to rollback_manager's internal cleaner.
        """
        for key in ("plugin_backup", "manifest_backup"):
            backup_path = snapshot.get(key)
            if backup_path and os.path.exists(backup_path):
                try:
                    os.remove(backup_path)
                    self.logger.debug(f"🧹 Cleaned backup: {backup_path}")
                except Exception as e:
                    self.logger.warning(f"Failed to delete backup {backup_path}: {e}")


# Global instance
plugin_rollback = PluginRollbackManager()