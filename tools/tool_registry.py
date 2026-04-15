"""
tools/tool_registry.py
───────────────────────
Central registry for every execution primitive in Operonix.

Changes from previous version
──────────────────────────────
• get_tools_for_intent() now appends the OllamaTool catch-all as the
  last candidate whenever it is registered — no hardcoding of the
  fallback; it discovers itself via the _CATCH_ALL flag.
• auto_load_tools() reads CORE_TOOLS from settings and also tries to
  load 'ollama_tool' if OLLAMA_ENABLED is True (default True).
• Everything else is unchanged.
"""
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
    tool_type: str          # "plugin" | "api_tool" | "shell_tool" | "file_tool" | "ui_tool" | "ollama_tool" | custom
    priority: int = 50      # higher == tried first
    supported_apps: list[str] = field(default_factory=list)


class ToolRegistry:
    """
    Central registry for every execution primitive in Operonix.

    Priority convention (plugin > api > file > shell > ui > ollama):
        plugin      → 100
        api_tool    → 90
        file_tool   → 80
        shell_tool  → 70
        ui_tool     → 50
        ollama_tool → 10   ← universal LLM fallback, always last
        custom      → caller decides
    """

    _DEFAULT_PRIORITIES: dict[str, int] = {
        "plugin":      100,
        "api_tool":    90,
        "file_tool":   80,
        "shell_tool":  70,
        "ui_tool":     50,
        "ollama_tool": 10,   # always lowest — catch-all of last resort
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
        from core.config import settings
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
        self.register_tool(name, instance, tool_type="plugin", priority=100)

    def get_tool(self, name: str) -> Optional[Any]:
        entry = self._entries.get(name)
        return entry.instance if entry else None

    def get_entry(self, name: str) -> Optional[ToolEntry]:
        return self._entries.get(name)

    def list_tools(self) -> list[str]:
        return list(self._entries.keys())

    def get_all_tools(self) -> dict[str, Any]:
        return {name: e.instance for name, e in self._entries.items()}

    # ------------------------------------------------------------------ #
    #  Intent-aware lookup                                                 #
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
        priority.

        Catch-all tools (those with _CATCH_ALL = True, e.g. OllamaTool) are
        always appended last regardless of forced_type, unless excluded.
        This ensures native tools always win and the LLM is a true last resort.
        """
        exclude = set(exclude or [])
        results: list[ToolEntry] = []
        catchall_entries: list[ToolEntry] = []

        for entry in self._entries.values():
            if entry.name in exclude:
                continue

            tool = entry.instance

            # Separate catch-all tools so they always go last
            if getattr(tool, "_CATCH_ALL", False):
                catchall_entries.append(entry)
                continue

            if forced_type and entry.tool_type != forced_type:
                continue

            handles = False
            if hasattr(tool, "can_handle"):
                handles = bool(tool.can_handle(intent))
            elif hasattr(tool, "supported_intents"):
                handles = intent in tool.supported_intents

            if not handles:
                continue

            # Context-affinity boost
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

        # Append catch-all tools at the end (sorted among themselves by priority)
        # Skip catch-alls if forced_type is set and doesn't match
        catchall_entries.sort(key=lambda e: e.priority, reverse=True)
        for entry in catchall_entries:
            if forced_type and entry.tool_type != forced_type:
                continue
            results.append(entry)

        return results

    # ------------------------------------------------------------------ #
    #  Auto-load from settings                                             #
    # ------------------------------------------------------------------ #

    def auto_load_tools(self) -> None:
        """
        Dynamically imports every tool listed in settings.CORE_TOOLS.
        Also loads ollama_tool if OLLAMA_ENABLED is True (default: True).
        """
        from core.config import settings

        core_tools: list[str] = getattr(settings, "CORE_TOOLS", [
            "file_tool", "shell_tool", "ui_tool", "api_tool"
        ])

        # Dynamically add ollama_tool if enabled (not hardcoded in CORE_TOOLS)
        ollama_enabled: bool = getattr(settings, "OLLAMA_ENABLED", True)
        if ollama_enabled and "ollama_tool" not in core_tools:
            core_tools = list(core_tools) + ["ollama_tool"]

        for tool_name in core_tools:
            try:
                module = importlib.import_module(f"tools.{tool_name}")
                instance = getattr(module, tool_name, None)
                if instance is None:
                    logger.warning(
                        f"Module 'tools.{tool_name}' has no instance named '{tool_name}'"
                    )
                    continue
                self.register_tool(tool_name, instance, tool_type=tool_name)
            except ImportError as exc:
                logger.error(f"Failed to import tool '{tool_name}': {exc}")


# ── Global singleton ──────────────────────────────────────────────────── #
tool_registry = ToolRegistry()
tool_registry.auto_load_tools()