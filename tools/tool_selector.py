"""
tools/tool_selector.py
───────────────────────
Consults tool_registry and returns the best tool for a given intent,
respecting the plugin > api > file > shell > ui > ollama priority ladder.

Changes from previous version
──────────────────────────────
• select_best_tool() now always returns a valid tool — if no native tool
  matches, OllamaTool (registered at priority 10) is returned automatically
  because get_tools_for_intent() appends catch-all tools at the end.
  No explicit Ollama check is needed here; it just works.

• Added select_with_fallback_chain():
  Executes the full fallback chain in order, returning the first success.
  This is the recommended entry point for the Executor — it is robust,
  event-bus aware, and requires zero manual try/catch.

• All methods pass enriched context to the registry so affinity boosts work.
"""
from __future__ import annotations

import logging
from typing import Any, Optional, Tuple

from core.event_bus import bus
from tools.tool_registry import tool_registry, ToolEntry

logger = logging.getLogger("ToolSelector")


class ToolSelector:
    """
    Selects and optionally executes the best tool for a given intent.

    Priority ladder (enforced by ToolRegistry, not here):
        plugin > api_tool > file_tool > shell_tool > ui_tool > ollama_tool
    """

    # ------------------------------------------------------------------ #
    #  Single best-tool lookup                                             #
    # ------------------------------------------------------------------ #

    async def select_best_tool(
        self,
        intent_data: dict,
        active_context: dict,
        exclude: Optional[list[str]] = None,
        forced_type: Optional[str] = None,
    ) -> Tuple[Optional[str], Optional[Any]]:
        """
        Returns (tool_type_str, tool_instance) for the highest-priority
        capable tool.

        Because OllamaTool self-declares _CATCH_ALL=True and has priority 10,
        it is always the last candidate — so this method never returns
        (None, None) as long as ollama_tool is registered.
        """
        intent: str    = intent_data.get("intent", "")
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
        is_llm_fallback = getattr(best.instance, "_CATCH_ALL", False)

        if is_llm_fallback:
            logger.info(
                f"🤖 No native tool matched intent='{intent}' — "
                f"routing to OllamaTool LLM fallback"
            )
        else:
            logger.info(
                f"✅ Selected '{best.name}' (type={best.tool_type} | "
                f"priority={best.priority}) for intent='{intent}'"
            )

        return best.tool_type, best.instance

    # ------------------------------------------------------------------ #
    #  Full fallback chain lookup                                          #
    # ------------------------------------------------------------------ #

    async def select_fallback_chain(
        self,
        intent_data: dict,
        active_context: dict,
        exclude: Optional[list[str]] = None,
    ) -> list[Tuple[str, Any]]:
        """
        Returns the full ordered chain of tools that can handle this intent.
        OllamaTool will always be the last entry in the chain.
        """
        intent: str    = intent_data.get("intent", "")
        active_app: str = active_context.get("active_window", "")

        candidates = tool_registry.get_tools_for_intent(
            intent=intent,
            active_app=active_app,
            exclude=exclude,
        )
        return [(e.tool_type, e.instance) for e in candidates]

    # ------------------------------------------------------------------ #
    #  Execute with full fallback chain (recommended entry point)          #
    # ------------------------------------------------------------------ #

    async def select_with_fallback_chain(
        self,
        intent_data: dict,
        active_context: dict,
        action: str = "",
        args: Optional[dict] = None,
    ) -> Tuple[bool, Any]:
        """
        Tries each tool in the priority chain until one succeeds.

        This is the most robust way to call the tool layer:
          1. Tries the highest-priority native tool first.
          2. On failure, tries the next tool in the chain.
          3. OllamaTool is always the final fallback.

        Parameters
        ──────────
        intent_data    : {"intent": str, ...}
        active_context : {"active_window": str, ...}
        action         : the action string passed to tool.run()
                         (defaults to intent if not provided)
        args           : the args dict passed to tool.run()

        Returns
        ───────
        (True, result)  on first success
        (False, errors) if all tools fail (list of per-tool error messages)
        """
        intent: str = intent_data.get("intent", "")
        if not action:
            action = intent
        args = args or {}

        chain = await self.select_fallback_chain(intent_data, active_context)

        if not chain:
            return False, f"No tools available for intent='{intent}'"

        errors: list[str] = []
        tried: list[str] = []

        for tool_type, tool_instance in chain:
            tool_name = getattr(tool_instance, "name", tool_type)
            tried.append(tool_name)
            is_llm = getattr(tool_instance, "_CATCH_ALL", False)

            logger.info(
                f"{'🤖 LLM fallback' if is_llm else '🔧 Trying'} "
                f"tool='{tool_name}' for intent='{intent}'"
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
                logger.info(f"✅ '{tool_name}' succeeded for intent='{intent}'")
                return True, result

            err_msg = f"{tool_name}: {result}"
            errors.append(err_msg)
            logger.warning(f"⚠️  '{tool_name}' failed — {result}")

            await bus.emit(
                "tool_failed",
                {"tool": tool_name, "intent": intent, "error": str(result)},
                source="tool_selector",
            )

        # All tools failed
        summary = " | ".join(errors)
        logger.error(f"❌ All tools failed for intent='{intent}': {summary}")
        return False, {"tried": tried, "errors": errors}


# ── Global singleton ──────────────────────────────────────────────────── #
tool_selector = ToolSelector()