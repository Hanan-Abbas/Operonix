"""
capabilities/bootstrap.py
──────────────────────────
Loads all *_ops modules into the global capability registry, attaches
intent-scoped validators, and ensures OllamaTool is registered.

Changes from previous version
──────────────────────────────
• Calls _ensure_ollama_registered() after capability init so OllamaTool
  is always in the tool registry when Operonix boots.
• OLLAMA_ENABLED in settings can disable this (default: True).
• Everything else is identical to the original.
"""
import logging

import capabilities as capabilities_pkg
from capabilities.registry import capability_registry
from capabilities import validation_rules as vr

logger = logging.getLogger("Bootstrap")


def init_capabilities() -> None:
    """Main entry point — called once at agent startup (core/main.py)."""
    # 1. Register all *_ops capabilities
    capability_registry.auto_register_ops(capabilities_pkg)

    # 2. Attach intent-scoped validation rules
    for intent, rules in vr.INTENT_VALIDATION.items():
        for rule in rules:
            capability_registry.add_intent_validation(intent, rule)

    logger.info("✅ Capabilities initialised.")

    # 3. Ensure OllamaTool is registered in the tool registry
    _ensure_ollama_registered()


def _ensure_ollama_registered() -> None:
    """
    Registers OllamaTool in ToolRegistry if OLLAMA_ENABLED is True.

    This is a safety net: ToolRegistry.auto_load_tools() already does this,
    but calling it here guarantees it even if bootstrap runs before
    auto_load_tools completes (e.g. in test environments).
    """
    try:
        from core.config import settings
        if not getattr(settings, "OLLAMA_ENABLED", True):
            logger.info("ℹ️  OllamaTool disabled via OLLAMA_ENABLED=False — skipping.")
            return

        from tools.tool_registry import tool_registry
        if tool_registry.get_tool("ollama_tool") is not None:
            return  # Already registered by auto_load_tools()

        from tools.ollama_tool import ollama_tool
        tool_registry.register_tool(
            "ollama_tool",
            ollama_tool,
            tool_type="ollama_tool",
            priority=10,
        )
        logger.info("✅ OllamaTool registered as universal LLM fallback (priority=10).")

    except ImportError as exc:
        logger.warning(f"Could not register OllamaTool: {exc}")
    except Exception as exc:
        logger.error(f"Unexpected error registering OllamaTool: {exc}", exc_info=True)