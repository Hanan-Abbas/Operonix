from __future__ import annotations
 
import logging
from typing import Any, Optional, Tuple
 
from tools.tool_registry import tool_registry, ToolEntry
 
logger = logging.getLogger("ToolSelector")
 
 
class ToolSelector:
    """
    Consults tool_registry and returns the best tool for a given intent,
    respecting the plugin>api>commands>ui priority ladder.
 
    Self-evolving hook
    ──────────────────
    If the learning system publishes `tool_affinity_update` events, the
    ToolRegistry's per-tool priorities can be patched at runtime; this
    selector will automatically reflect those changes on the next call.
    """
 
    async def select_best_tool(
        self,
        intent_data: dict,
        active_context: dict,
        exclude: Optional[list[str]] = None,
        forced_type: Optional[str] = None,
    ) -> Tuple[Optional[str], Optional[Any]]:
        """
        Returns (tool_type_str, tool_instance) for the highest-priority
        capable tool, or (None, None) if nothing can handle the intent.
 
        Parameters
        ──────────
        intent_data    : dict with at least {"intent": str}
        active_context : dict with at least {"active_window": str}
        exclude        : tool names to skip (used by FallbackManager)
        forced_type    : if set, only tools of this type are considered
        """
        intent: str = intent_data.get("intent", "")
        active_app: str = active_context.get("active_window", "")
 
        candidates: list[ToolEntry] = tool_registry.get_tools_for_intent(
            intent=intent,
            active_app=active_app,
            exclude=exclude,
            forced_type=forced_type,
        )
 
        if not candidates:
            logger.warning(f"⚠️  No tool found for intent='{intent}' app='{active_app}'")
            return None, None
 
        best = candidates[0]
        logger.info(
            f"✅ Selected '{best.name}' (type={best.tool_type} | "
            f"priority={best.priority}) for intent='{intent}'"
        )
        return best.tool_type, best.instance
 
    async def select_fallback_chain(
        self,
        intent_data: dict,
        active_context: dict,
        exclude: Optional[list[str]] = None,
    ) -> list[Tuple[str, Any]]:
        """
        Returns the full ordered chain of tools that can handle this intent.
        FallbackManager iterates this list and tries each one in sequence.
        """
        intent: str = intent_data.get("intent", "")
        active_app: str = active_context.get("active_window", "")
 
        candidates = tool_registry.get_tools_for_intent(
            intent=intent,
            active_app=active_app,
            exclude=exclude,
        )
        return [(e.tool_type, e.instance) for e in candidates]
 
 
# ── Global singleton ──────────────────────────────────────────────────── #
tool_selector = ToolSelector()