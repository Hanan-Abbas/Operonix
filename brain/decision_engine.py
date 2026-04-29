"""
brain/decision_engine.py
─────────────────────────
Traffic cop of the AI — prioritises tasks and selects the execution pipeline
tier (plugin -> api -> command -> ui).

Changes from original
──────────────────────
BUG FIX — _resolve_execution_tool()
    The original heuristic used brittle intent-prefix matching.  "create_dir"
    starts with none of the recognised prefixes so it fell through to the
    "api_tool" catch-all — wrong.

    New approach:
    1. If CapabilityMapper already resolved a suggested_tool from ops metadata,
       trust it completely — no second-guessing.
    2. Otherwise run the prefix heuristic, but with an extended prefix table
       that covers directory / file operations.
    3. The heuristic now maps to *tool_type* strings that match what
       ToolRegistry uses, not arbitrary names.

    This keeps the file fully flexible: adding a new ops module with its own
    CAPABILITY_METADATA means zero changes here.
"""
from __future__ import annotations

import asyncio
import logging
from typing import Any

from core.config import settings
from core.event_bus import bus
from capabilities.registry import capability_registry


class DecisionEngine:
    """
    Prioritises tasks, resolves execution pathways, and determines the best
    tool via the dynamic pipeline (Plugin -> API -> Command -> UI).
    """

    def __init__(self) -> None:
        self.logger = logging.getLogger("DecisionEngine")
        self.task_queue: asyncio.PriorityQueue = asyncio.PriorityQueue()
        self.active_tasks: dict = {}

        # Priority scores by intent prefix — lowest wins queue ordering
        # (we invert scores when inserting into PriorityQueue)
        self.PREFIX_PRIORITIES: dict[str, int] = {
            "emergency": 100,
            "security":  90,
            "stop":      90,
            "cancel":    85,
            "voice_":    50,
            "ui_":       30,
            "click":     30,
            "type_":     30,
            "file_":     20,
            "read_":     20,
            "write_":    20,
            "create_":   20,   # covers create_dir, create_file, etc.
            "delete_":   20,   # covers delete_dir, delete_file
            "move_":     20,
            "list_":     20,
            "append_":   20,
            "search_":   10,
            "web_":      10,
        }

        # Intent -> tool_type fallback table.
        # Used ONLY when CapabilityMapper did not supply a suggested_tool.
        # Maps intent *substrings* (checked with `in`) to tool_type strings
        # that match ToolRegistry registrations.
        self._INTENT_TOOL_MAP: dict[str, str] = {
            # File / directory operations -> native file_tool or shell_tool
            "write_file":    "file_tool",
            "append_file":   "file_tool",
            "read_file":     "file_tool",
            "delete_file":   "file_tool",
            "move_file":     "file_tool",
            "list_dir":      "file_tool",
            "create_dir":    "shell_tool",   # mkdir via shell
            "delete_dir":    "shell_tool",   # rmdir via shell
            # Shell / CLI
            "run_command":   "shell_tool",
            "execute_script":"shell_tool",
            "git_op":        "shell_tool",
            "install_package":"shell_tool",
            "check_status":  "shell_tool",
            # Web
            "open_url":      "api_tool",
            "search_web":    "api_tool",
            # UI
            "click":         "ui_tool",
            "type_text":     "ui_tool",
            "scroll":        "ui_tool",
            "move_cursor":   "ui_tool",
        }

    # ── Lifecycle ─────────────────────────────────────────────────────── #

    async def start(self) -> None:
        bus.subscribe("capability_mapped", self.enqueue_task)
        asyncio.create_task(self._process_queue())
        self.logger.info("Decision Engine: Online. Listening to Capability Mapper.")

    # ── Queuing ───────────────────────────────────────────────────────── #

    async def enqueue_task(self, event: Any) -> None:
        task_data = event.data
        intent = task_data.get("intent", "")
        task_id = task_data.get("task_id")
        priority_score = self._calculate_priority(intent, task_data)
        await self.task_queue.put((-priority_score, task_data))
        self.logger.info(
            "Task [%s] (%s) queued — priority=%d", task_id, intent, priority_score
        )

    def _calculate_priority(self, intent: str, task_data: dict) -> int:
        intent_lower = (intent or "").lower()
        score = 15  # baseline
        for prefix, weight in self.PREFIX_PRIORITIES.items():
            if intent_lower.startswith(prefix):
                score = weight
                break
        if task_data.get("source") == "user_foreground":
            score += 25
        return score

    # ── Tool resolution (BUG FIX) ─────────────────────────────────────── #

    async def _resolve_execution_tool(
        self, intent: str, context: dict, suggested_tool: str | None
    ) -> str:
        """
        Determine the best execution tool for this intent.

        Priority:
          1. suggested_tool from CapabilityMapper ops metadata  — trust it.
          2. Active app plugin (app-specific plugin registered for this app).
          3. _INTENT_TOOL_MAP exact lookup.
          4. Prefix heuristics (extended to cover dir/file ops).
          5. "api_tool" as the final catch-all.
        """
        # 1. Trust the mapper's metadata resolution
        if suggested_tool:
            self.logger.debug(
                "Using mapper-supplied tool for '%s': %s", intent, suggested_tool
            )
            return suggested_tool

        # 2. Active app plugin check
        active_app = context.get("active_window", "")
        if active_app:
            # plugin_registry.get_for_app() — safe import so this doesn't
            # break if the plugin system is not yet initialised
            try:
                from plugins.registry import plugin_registry          # type: ignore
                plugin = plugin_registry.get_for_app(active_app, intent)
                if plugin:
                    return "plugin"
            except Exception:
                pass

        # 3. Exact intent lookup in the static fallback table
        exact = self._INTENT_TOOL_MAP.get(intent)
        if exact:
            self.logger.debug(
                "Resolved tool for '%s' via intent map: %s", intent, exact
            )
            return exact

        # 4. Prefix heuristics (substring check for flexibility)
        intent_lower = intent.lower()
        if any(x in intent_lower for x in ("file", "read", "write", "append")):
            return "file_tool"
        if any(x in intent_lower for x in ("dir", "folder", "mkdir", "rmdir")):
            return "shell_tool"
        if any(x in intent_lower for x in ("run", "execute", "git", "install", "cmd")):
            return "shell_tool"
        if any(x in intent_lower for x in ("click", "type", "scroll", "move_cursor")):
            return "ui_tool"
        if any(x in intent_lower for x in ("url", "web", "http", "search")):
            return "api_tool"

        # 5. Final catch-all
        self.logger.debug(
            "No specific tool rule for '%s' — falling back to api_tool", intent
        )
        return "api_tool"

    # ── Queue processor ───────────────────────────────────────────────── #

    async def _process_queue(self) -> None:
        while True:
            try:
                _priority, task_data = await self.task_queue.get()
                task_id = task_data.get("task_id")
                intent = task_data.get("intent", "")
                context = task_data.get("context", {})

                # suggested_tool comes from CapabilityMapper (may be None)
                mapper_tool: str | None = task_data.get("suggested_tool")

                resolved_tool = await self._resolve_execution_tool(
                    intent, context, mapper_tool
                )
                task_data["suggested_tool"] = resolved_tool

                self.logger.info(
                    "Decision Engine: Task [%s] (%s) -> tool='%s'",
                    task_id, intent, resolved_tool,
                )

                bus.publish(
                    "request_planning",
                    data=task_data,
                    source="decision_engine",
                )

                self.task_queue.task_done()

            except asyncio.CancelledError:
                break
            except Exception as exc:
                self.logger.error("Queue processor error: %s", exc)
                await asyncio.sleep(1)


decision_engine = DecisionEngine()