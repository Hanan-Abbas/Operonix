"""
plugins/registry.py
────────────────────
Single source of truth for all installed plugins and their runtime state.

Changes from original
──────────────────────
All original functionality is preserved verbatim:
  • register() / unregister() / _register_as_capability()
  • get() / get_by_intent() / list_all() / list_trusted() / list_by_status()
  • update_status() / revoke_trust() / increment_stats() / summary()
  • Thread-safe _lock, PluginEntry, entries property

Plan addition — can_handle(intent) -> float  (Plan §7, MODIFY)
───────────────────────────────────────────────────────────────
MethodRouter._evaluate_plugin() needs a confidence score per plugin
rather than the boolean from tool_registry.  Added to PluginRegistry:

  can_handle(intent_str) -> float
    Scores every trusted plugin against *intent_str* using
    brain.intent_matcher.match_intent_local() and returns the highest
    score found.  Returns 0.0 if no trusted plugins are registered or
    if no plugin scores above settings.INTENT_MATCH_MIN_CONFIDENCE.

    This is a synchronous read-only method — no lock needed beyond what
    list_trusted() already holds internally.

    The router calls this as a fast pre-check before calling
    list_trusted() + match_intent_local() individually.  The result is
    used ONLY as a gate: if can_handle() returns 0.0, the router skips
    the plugin layer entirely without iterating all entries.

Added to PluginEntry:

  score_for_intent(intent_str) -> float
    Per-entry scoring method called by can_handle().  Scores the entry's
    declared capabilities list against the intent using match_intent_local().
    Returns 0.0 if capabilities is empty.
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
        manifest   : PluginManifest,
        instance   : BasePlugin | None = None,
        plugin_dir : str = "",
    ) -> None:
        self.manifest   = manifest
        self.instance   = instance
        self.plugin_dir = plugin_dir
        self.loaded_at  = datetime.utcnow().isoformat()

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
            "name":         self.name,
            "status":       self.manifest.status.value,
            "intent":       self.manifest.intent,
            "version":      self.manifest.version,
            "trusted":      self.manifest.trusted,
            "risk_level":   self.manifest.risk_level.value,
            "success_rate": self.manifest.success_rate,
            "total_runs":   self.manifest.total_runs,
            "loaded_at":    self.loaded_at,
        }

    # ── Plan addition ─────────────────────────────────────────────────────────

    def score_for_intent(self, intent_str: str) -> float:
        """
        Score this plugin's capability coverage against *intent_str*.

        Uses match_intent_local() (brain/intent_matcher.py) against every
        declared capability and the plugin's primary intent field.  Returns
        the highest score found, or 0.0 if no capabilities are declared.

        This method is called by PluginRegistry.can_handle() to find the
        best-matching plugin across all trusted entries.
        """
        from brain.intent_matcher import match_intent_local
        from core.config import settings

        min_confidence: float = float(
            getattr(settings, "INTENT_MATCH_MIN_CONFIDENCE", 0.35)
        )

        capabilities: list[str] = list(
            getattr(self.manifest, "capabilities", []) or []
        )
        primary_intent: str = getattr(self.manifest, "intent", "") or ""
        if primary_intent and primary_intent not in capabilities:
            capabilities.append(primary_intent)

        if not capabilities:
            return 0.0

        _matched, score = match_intent_local(
            candidate_text=intent_str,
            allowed_intents=capabilities,
            threshold=min_confidence,
        )
        return float(score)


class PluginRegistry:
    """
    Central registry for all installed plugins.

    Access pattern:
        plugin_registry.register(entry)
        plugin_registry.get("plugin_name")
        plugin_registry.get_by_intent("search_web")
        plugin_registry.list_trusted()
        plugin_registry.can_handle("open_file")  # new — for MethodRouter
    """

    def __init__(self) -> None:
        self._entries : dict[str, PluginEntry] = {}
        self._lock    : threading.RLock = threading.RLock()

    @property
    def entries(self) -> dict[str, PluginEntry]:
        """Public read access to the entries dict (thread-safe snapshot)."""
        with self._lock:
            return dict(self._entries)

    # ── Registration (unchanged) ──────────────────────────────────────────────

    def register(self, entry: PluginEntry) -> bool:
        with self._lock:
            existing = self._entries.get(entry.name)
            if existing:
                logger.info(
                    "Hot-reloading plugin '%s' (v%s → v%s)",
                    entry.name,
                    existing.manifest.version,
                    entry.manifest.version,
                )
            self._entries[entry.name] = entry
            logger.info(
                "Plugin registered: '%s' status=%s",
                entry.name, entry.manifest.status.value,
            )

        if entry.is_active and entry.instance:
            self._register_as_capability(entry)

        bus.publish("plugin_registered", entry.to_dict(), source="plugin_registry")
        return True

    def unregister(self, name: str) -> bool:
        with self._lock:
            if name not in self._entries:
                return False
            del self._entries[name]

        try:
            from capabilities.registry import capability_registry
            capability_registry.registry.pop(name, None)
            capability_registry.metadata.pop(name, None)
        except Exception as exc:
            logger.warning(
                "Could not remove '%s' from capability_registry: %s", name, exc
            )

        bus.publish(
            "plugin_unregistered", {"name": name}, source="plugin_registry"
        )
        logger.info("Plugin unregistered: '%s'", name)
        return True

    def _register_as_capability(self, entry: PluginEntry) -> None:
        try:
            from capabilities.registry import capability_registry

            async def _capability_wrapper(context: dict, args: dict) -> dict:
                return await entry.instance.run(context, args)

            _capability_wrapper.__name__ = entry.name

            capability_registry.register(
                entry.name,
                _capability_wrapper,
                metadata={
                    "description" : entry.manifest.description,
                    "method"      : "plugin",
                    "tags"        : entry.manifest.tags,
                    "version"     : entry.manifest.version,
                    "intent"      : entry.manifest.intent,
                    "plugin"      : True,
                },
            )
            logger.info(
                "Plugin '%s' injected into capability_registry.", entry.name
            )
        except Exception as exc:
            logger.error(
                "Failed to inject plugin '%s' into capability_registry: %s",
                entry.name, exc,
            )

    # ── Query interface (unchanged + can_handle addition) ─────────────────────

    def get(self, name: str) -> PluginEntry | None:
        with self._lock:
            return self._entries.get(name)

    def get_by_intent(self, intent: str) -> list[PluginEntry]:
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

    # ── Plan addition — scoring interface for MethodRouter ────────────────────

    def can_handle(self, intent_str: str) -> float:
        """
        Return the highest confidence score any trusted plugin can achieve
        for *intent_str*.  Returns 0.0 if no trusted plugins are registered
        or if no plugin scores above settings.INTENT_MATCH_MIN_CONFIDENCE.

        Called by MethodRouter._evaluate_plugin() as a fast gate before
        iterating all trusted entries individually.  If this returns 0.0,
        the router skips the plugin layer entirely.

        Thread-safe: reads from list_trusted() which holds _lock internally.
        """
        trusted = self.list_trusted()
        if not trusted:
            return 0.0

        best: float = 0.0
        for entry in trusted:
            score = entry.score_for_intent(intent_str)
            if score > best:
                best = score

        return best

    # ── State mutations (unchanged) ───────────────────────────────────────────

    def update_status(
        self, name: str, status: PluginStatus, plugin_dir: str = ""
    ) -> bool:
        with self._lock:
            entry = self._entries.get(name)
            if not entry:
                return False
            entry.manifest.status  = status
            entry.manifest.trusted = (status == PluginStatus.TRUSTED)
            entry.manifest.last_reviewed = datetime.utcnow().isoformat()
            if entry.plugin_dir or plugin_dir:
                entry.manifest.save(entry.plugin_dir or plugin_dir)

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
        logger.warning("Revoking trust for plugin '%s': %s", name, reason)
        bus.publish(
            "plugin_trust_revoked",
            {"name": name, "reason": reason},
            source="plugin_registry",
        )
        return self.update_status(name, PluginStatus.UNTRUSTED)

    def increment_stats(self, name: str, success: bool) -> None:
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

    def summary(self) -> dict:
        with self._lock:
            entries = list(self._entries.values())
        return {
            "total"    : len(entries),
            "trusted"  : sum(1 for e in entries if e.is_active),
            "pending"  : sum(1 for e in entries if e.status == PluginStatus.PENDING),
            "untrusted": sum(1 for e in entries if e.status == PluginStatus.UNTRUSTED),
            "retired"  : sum(1 for e in entries if e.status == PluginStatus.RETIRED),
        }


# Global singleton
plugin_registry = PluginRegistry()