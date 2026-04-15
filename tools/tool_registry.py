from __future__ import annotations
 
import importlib
import logging
from dataclasses import dataclass, field
from typing import Any, Optional
 
logger = logging.getLogger("ToolRegistry")
 
 
@dataclass
class ToolEntry:
    name: str
    instance: Any
    tool_type: str          # "plugin" | "api_tool" | "shell_tool" | "file_tool" | "ui_tool" | custom
    priority: int = 50      # higher == tried first; mirrors old PRIORITY_MAP
    supported_apps: list[str] = field(default_factory=list)
 
 
class ToolRegistry:
    """
    Central registry for every execution primitive in Operonix.
 
    Priority convention (matches the plugin>api>commands>ui flow):
        plugin      → 100
        api_tool    → 90
        shell_tool  → 70   (commands layer)
        file_tool   → 80   (commands layer, slightly above shell)
        ui_tool     → 50   (ui fallback — last resort)
        custom      → caller decides
    """
 
    # Default priorities; overridable via settings.TOOL_PRIORITIES
    _DEFAULT_PRIORITIES: dict[str, int] = {
        "plugin":     100,
        "api_tool":   90,
        "file_tool":  80,
        "shell_tool": 70,
        "ui_tool":    50,
    }
 
    def __init__(self) -> None:
        self._entries: dict[str, ToolEntry] = {}
 
    # ------------------------------------------------------------------ #
    #  Registration                                                         #
    # ------------------------------------------------------------------ #
 
    def register_tool(
        self,
        name: str,
        instance: Any,
        tool_type: str = "unknown",
        priority: Optional[int] = None,
        supported_apps: Optional[list[str]] = None,
    ) -> None:
        """Register any execution primitive (tool, plugin, API adapter)."""
        # Pull priority from config overrides → type defaults → caller value → 50
        from core.config import settings  # lazy import to avoid circular deps
        overrides: dict[str, int] = getattr(settings, "TOOL_PRIORITIES", {})
        resolved_priority = (
            overrides.get(name)
            or overrides.get(tool_type)
            or priority
            or self._DEFAULT_PRIORITIES.get(tool_type, 50)
        )
        self._entries[name] = ToolEntry(
            name=name,
            instance=instance,
            tool_type=tool_type,
            priority=resolved_priority,
            supported_apps=supported_apps or getattr(instance, "supported_apps", []),
        )
        logger.info(f"✅ Registered tool '{name}' | type={tool_type} | priority={resolved_priority}")
 
    def register_plugin(self, name: str, instance: Any) -> None:
        """Convenience wrapper — plugins always land at the top of the stack."""
        self.register_tool(name, instance, tool_type="plugin", priority=100)
 
    def get_tool(self, name: str) -> Optional[Any]:
        entry = self._entries.get(name)
        return entry.instance if entry else None
 
    def get_entry(self, name: str) -> Optional[ToolEntry]:
        return self._entries.get(name)
 
    def list_tools(self) -> list[str]:
        return list(self._entries.keys())
 
    def get_all_tools(self) -> dict[str, Any]:
        """Legacy-compatible: returns {name: instance} dict."""
        return {name: e.instance for name, e in self._entries.items()}
 
    # ------------------------------------------------------------------ #
    #  Intent-aware lookup  (replaces _tool_matches_intent)                #
    # ------------------------------------------------------------------ #
 
    def get_tools_for_intent(
        self,
        intent: str,
        active_app: str = "",
        exclude: Optional[list[str]] = None,
        forced_type: Optional[str] = None,
    ) -> list[ToolEntry]:
        """
        Return all entries capable of handling *intent*, sorted by descending
        priority.  No hardcoded intent→tool mapping lives here — each tool
        self-declares via:
            • tool.supported_intents  (set/list)   OR
            • tool.can_handle(intent) (callable)
        Tools that declare neither are skipped (safe default).
        """
        exclude = set(exclude or [])
        results: list[ToolEntry] = []
 
        for entry in self._entries.values():
            if entry.name in exclude:
                continue
            if forced_type and entry.tool_type != forced_type:
                continue
 
            tool = entry.instance
            handles = False
 
            if hasattr(tool, "can_handle"):
                handles = bool(tool.can_handle(intent))
            elif hasattr(tool, "supported_intents"):
                handles = intent in tool.supported_intents
            # tools with neither declaration are invisible to intent routing
 
            if not handles:
                continue
 
            # Context-affinity boost: bump priority if the tool knows this app
            boosted_priority = entry.priority
            if active_app and active_app in entry.supported_apps:
                boosted_priority += 20
 
            results.append(
                ToolEntry(
                    name=entry.name,
                    instance=tool,
                    tool_type=entry.tool_type,
                    priority=boosted_priority,
                    supported_apps=entry.supported_apps,
                )
            )
 
        results.sort(key=lambda e: e.priority, reverse=True)
        return results
 
    # ------------------------------------------------------------------ #
    #  Auto-load from settings                                             #
    # ------------------------------------------------------------------ #
 
    def auto_load_tools(self) -> None:
        """
        Dynamically imports every tool listed in settings.CORE_TOOLS.
        Each module must expose a module-level instance with the same name
        as the module (e.g. tools/file_tool.py → file_tool instance).
        """
        from core.config import settings
        core_tools: list[str] = getattr(settings, "CORE_TOOLS", [
            "file_tool", "shell_tool", "ui_tool", "api_tool"
        ])
 
        for tool_name in core_tools:
            try:
                module = importlib.import_module(f"tools.{tool_name}")
                instance = getattr(module, tool_name, None)
                if instance is None:
                    logger.warning(f"Module 'tools.{tool_name}' has no instance named '{tool_name}'")
                    continue
                # Infer type from module name convention (e.g. "file_tool" → "file_tool")
                self.register_tool(tool_name, instance, tool_type=tool_name)
            except ImportError as exc:
                logger.error(f"Failed to import tool '{tool_name}': {exc}")
 
 
# ── Global singleton ──────────────────────────────────────────────────── #
tool_registry = ToolRegistry()
tool_registry.auto_load_tools()
 