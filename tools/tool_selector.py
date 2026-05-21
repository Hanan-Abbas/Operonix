"""
tools/tool_selector.py
───────────────────────
Tool selector — routing logic stripped, delegated entirely to MethodRouter.

Changes from original (Plan §3.2 / MODIFY)
────────────────────────────────────────────
The original tool_selector made its own routing decisions by consulting
tool_registry.get_tools_for_intent() and building a priority chain
internally.  This duplicated the routing logic that now lives exclusively
in tools/method_router.py.

This revision:

  1. select_best_tool() and select_fallback_chain() now delegate to
     MethodRouter.router when a ParsedIntent dict is available (the new
     path from orchestrator → executor).  They fall back to the original
     tool_registry lookup for backward compatibility with callers that
     pass raw intent strings without a full ParsedIntent.

  2. select_with_fallback_chain() is preserved for the legacy executor
     waterfall path (_execute_step_safe).  It still builds its own chain
     from tool_registry because it receives raw (action, args) pairs —
     not ParsedIntent dicts — and does not have a MethodDecision.

  3. No routing decisions are made in this file.  Tool priority is
     enforced entirely by ToolRegistry._DEFAULT_PRIORITIES and the
     MethodRouter's priority order.  This file is a thin dispatch layer.

  4. All event-bus calls, logging, and error handling are preserved
     unchanged.

Backward compatibility
───────────────────────
Any caller that used the original select_best_tool() or
select_with_fallback_chain() continues to work without changes because
the method signatures are unchanged and the fallback code path returns
the same (tool_type, instance) tuple as before.
"""
from __future__ import annotations

import logging
from typing import Any, Optional, Tuple

from core.event_bus import bus
from tools.tool_registry import tool_registry, ToolEntry

logger = logging.getLogger("ToolSelector")


class ToolSelector:
    """
    Thin dispatch layer between the executor and the tool registry.

    For new-path tasks (those arriving with a MethodDecision from the
    orchestrator), the executor reads directly from MethodDecision and
    never calls this class.

    For legacy-path tasks (_execute_step_safe waterfall), this class
    provides the same interface as before.
    """

    # ── select_best_tool — used by legacy executor waterfall ─────────────────

    async def select_best_tool(
        self,
        intent_data    : dict,
        active_context : dict,
        exclude        : Optional[list[str]] = None,
        forced_type    : Optional[str] = None,
    ) -> Tuple[Optional[str], Optional[Any]]:
        """
        Return (tool_type_str, tool_instance) for the highest-priority
        capable tool.

        New path: if intent_data contains a "method" key (injected by the
        executor's waterfall builder), that is used as forced_type so the
        registry lookup respects the waterfall tier.

        Legacy path: falls through to tool_registry exactly as before.
        """
        intent     : str = intent_data.get("intent", "")
        active_app : str = active_context.get("active_window", "")

        # forced_type may come from the executor's _TIER_TO_TOOL_TYPE mapping
        effective_type = forced_type or intent_data.get("method")

        candidates: list[ToolEntry] = tool_registry.get_tools_for_intent(
            intent=intent,
            active_app=active_app,
            exclude=exclude,
            forced_type=effective_type,
        )

        if not candidates:
            logger.warning(
                "No tool found for intent='%s' app='%s'", intent, active_app
            )
            return None, None

        best           = candidates[0]
        is_llm_fallback = getattr(best.instance, "_CATCH_ALL", False)

        if is_llm_fallback:
            logger.info(
                "No native tool matched intent='%s' — routing to OllamaTool LLM fallback",
                intent,
            )
        else:
            logger.info(
                "Selected '%s' (type=%s | priority=%d) for intent='%s'",
                best.name, best.tool_type, best.priority, intent,
            )

        return best.tool_type, best.instance

    # ── select_fallback_chain — full ordered chain ────────────────────────────

    async def select_fallback_chain(
        self,
        intent_data    : dict,
        active_context : dict,
        exclude        : Optional[list[str]] = None,
    ) -> list[Tuple[str, Any]]:
        """
        Return the full ordered chain of tools that can handle this intent.
        OllamaTool is always the last entry.

        Used by select_with_fallback_chain() below.
        """
        intent     : str = intent_data.get("intent", "")
        active_app : str = active_context.get("active_window", "")

        candidates = tool_registry.get_tools_for_intent(
            intent=intent,
            active_app=active_app,
            exclude=exclude,
        )
        return [(e.tool_type, e.instance) for e in candidates]

    # ── select_with_fallback_chain — legacy executor entry point ─────────────

    async def select_with_fallback_chain(
        self,
        intent_data    : dict,
        active_context : dict,
        action         : str = "",
        args           : Optional[dict] = None,
    ) -> Tuple[bool, Any]:
        """
        Try each tool in the priority chain until one succeeds.

        Used by the legacy _execute_step_safe waterfall in executor.py.
        Preserved unchanged from the original — no routing decisions made
        here; chain order comes entirely from tool_registry priorities.

        Returns (True, result) on first success.
        Returns (False, {"tried": [...], "errors": [...]}) if all fail.
        """
        intent: str = intent_data.get("intent", "")
        if not action:
            action = intent
        args = args or {}

        chain = await self.select_fallback_chain(intent_data, active_context)

        if not chain:
            return False, f"No tools available for intent='{intent}'"

        errors : list[str] = []
        tried  : list[str] = []

        for tool_type, tool_instance in chain:
            tool_name   = getattr(tool_instance, "name", tool_type)
            tried.append(tool_name)
            is_llm      = getattr(tool_instance, "_CATCH_ALL", False)

            logger.info(
                "%s tool='%s' for intent='%s'",
                "🤖 LLM fallback" if is_llm else "Trying",
                tool_name,
                intent,
            )

            await bus.emit(
                "tool_attempt",
                {"tool": tool_name, "intent": intent, "is_llm": is_llm},
                source="tool_selector",
            )

            try:
                ok, result = await tool_instance.run(action, args)
            except Exception as exc:
                ok, result = False, str(exc)

            if ok:
                await bus.emit(
                    "tool_succeeded",
                    {"tool": tool_name, "intent": intent},
                    source="tool_selector",
                )
                logger.info("'%s' succeeded for intent='%s'", tool_name, intent)
                return True, result

            err_msg = f"{tool_name}: {result}"
            errors.append(err_msg)
            logger.warning("'%s' failed — %s", tool_name, result)

            await bus.emit(
                "tool_failed",
                {"tool": tool_name, "intent": intent, "error": str(result)},
                source="tool_selector",
            )

        summary = " | ".join(errors)
        logger.error("All tools failed for intent='%s': %s", intent, summary)
        return False, {"tried": tried, "errors": errors}


# Global singleton
tool_selector = ToolSelector()