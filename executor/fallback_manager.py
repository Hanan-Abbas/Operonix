import logging
from core.config import settings
from core.event_bus import bus

logger = logging.getLogger("FallbackManager")


class FallbackManager:

    def __init__(self):
        # We read the baseline priority directly from your core/config.py!
        # This keeps the scoring dynamic.
        self.priority_map = getattr(
            settings,
            "TOOL_PRIORITY",
            {"plugin": 5, "api": 4, "file": 3, "shell": 2, "ui": 1},
        )

    def get_fallback(self, current_tool_type: str) -> str:
        """Determines the next logical tool type to fallback to when the current

        one fails.

        Example: If an API tool fails, fall back to a Shell command. If that
        fails, try UI automation.
        """
        # We can look at the priority map and find the next best tier below the current one
        current_priority = self.priority_map.get(current_tool_type, 0)

        # Find all tiers that have a lower priority than what just failed
        candidates = [
            (type_str, priority)
            for type_str, priority in self.priority_map.items()
            if priority < current_priority
        ]

        if not candidates:
            logger.warning(
                f"No lower fallback tiers available for {current_tool_type}"
            )
            return None

        # Sort them so the next highest score is first
        candidates.sort(key=lambda x: x[1], reverse=True)

        # Return the string of the next best tool type (e.g., 'shell' or 'ui')
        next_best_type = candidates[0][0]
        return next_best_type

    def score_tool(self, tool, context=None) -> float:
        """Scores an individual tool's capability based on priority,

        reliability, and latency.
        """
        tool_type = getattr(tool, "type", "ui")
        priority = self.priority_map.get(tool_type, 1)

        # Dynamic metrics often updated by the execution_tracker/learning system
        reliability = getattr(tool, "success_rate", 1.0)
        latency = getattr(tool, "latency", 0.5)

        # Custom rules based on context
        bonus = 0
        if context and getattr(tool, "requires_ui", False):
            # If we need a UI but the system says no UI is active, tank the score!
            if not context.get("has_ui", True):
                return -100.0

        # Your custom scoring algorithm (Prioritizes type, rewards success, penalizes lag)
        score = (priority * 2) + (reliability * 10) - (latency * 2) + bonus
        return float(score)

    def get_next_tool(
        self, action: str, tried_tools: list, tool_registry, context=None
    ):
        """Advanced selection: Loops through all registered tools, scores them,

        and pulls the absolute best one that hasn't been tried yet.
        """
        candidates = []

        for tool in tool_registry.get_all_tools():
            # Skip if we already tried it and it failed
            if tool.name in tried_tools:
                continue

            # Skip if the tool itself says it can't do this specific action
            if hasattr(tool, "can_handle") and not tool.can_handle(action):
                continue

            # Calculate the score
            score = self.score_tool(tool, context)

            # Only add it if it didn't get disqualified (like missing a required UI)
            if score > -50:
                candidates.append((score, tool))

        if not candidates:
            return None

        # Sort by score descending
        candidates.sort(key=lambda x: x[0], reverse=True)
        selected = candidates[0][1]

        # Use our updated non-async publish method for the queue!
        bus.publish(
            "fallback_selected",
            data={"tool": selected.name, "action": action},
            source="fallback_manager",
        )

        return selected