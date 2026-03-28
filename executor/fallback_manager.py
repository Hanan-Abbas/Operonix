class FallbackManager:
    def __init__(self, tool_registry):
        self.tool_registry = tool_registry

    def get_next_tool(self, task, tried_tools, context=None, last_error=None):
        candidates = []

        for tool in self.tool_registry.get_all_tools():

            if tool.name in tried_tools:
                continue

            if not tool.can_handle(task):
                continue

            # Skip based on error type
            if last_error == "permission_denied" and tool.type in ["file", "shell"]:
                continue

            # Skip if UI not available
            if getattr(tool, "requires_ui", False) and context and not context.has_ui:
                continue

            # Hard rule example
            if task.type == "click" and tool.type in ["file", "shell"]:
                continue

            score = self.score_tool(tool)
            candidates.append((score, tool))

        if not candidates:
            return None

        candidates.sort(reverse=True, key=lambda x: x[0])
        selected = candidates[0][1]

        bus.emit("fallback_selected", {
            "tool": selected.name,
            "task": task.type
        })

        return selected

    def score_tool(self, tool):
        priority_map = {
            "plugin": 5,
            "api": 4,
            "file": 3,
            "shell": 2,
            "ui": 1
        }

        priority = priority_map.get(tool.type, 0)
        reliability = getattr(tool, "success_rate", 1)
        latency = getattr(tool, "latency", 1)

        return priority * 2 + reliability - latency
