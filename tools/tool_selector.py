import logging
from tools.tool_registry import tool_registry
from plugins.plugin_registry import plugin_registry

class ToolSelector:
    def __init__(self):
        self.logger = logging.getLogger("ToolSelector")

        self.PRIORITY_MAP = {
            "plugin": 100,
            "api_tool": 90,
            "file_tool": 80,
            "shell_tool": 70,
            "ui_tool": 50
        }

    async def select_best_tool(self, intent_data, active_context, exclude=None, forced_type=None):
        exclude = exclude or []
        intent = intent_data.get("intent")
        target_app = active_context.get("active_window")

        candidates = []

        # --- 1. Plugins ---
        plugins = plugin_registry.get_all_plugins_for_app(target_app)

        for plugin in plugins:
            if plugin.name in exclude:
                continue
            if forced_type and forced_type != "plugin":
                continue
            if plugin.supports_action(intent):
                candidates.append((self.PRIORITY_MAP["plugin"], "plugin", plugin))

        # --- 2. Tools ---
        for tool_name in tool_registry.list_tools():
            if tool_name in exclude:
                continue

            tool = tool_registry.get_tool(tool_name)

            if forced_type and tool_name != forced_type:
                continue

            if hasattr(tool, "can_handle"):
                if not tool.can_handle(intent_data):
                    continue
            else:
                if not self._tool_matches_intent(tool_name, intent):
                    continue

            score = self.PRIORITY_MAP.get(tool.type, 0)

            # Context boost
            if target_app in getattr(tool, "supported_apps", []):
                score += 20

            candidates.append((score, tool_name, tool))

        if not candidates:
            return None, None

        candidates.sort(key=lambda x: x[0], reverse=True)

        score, tool_type, tool_instance = candidates[0]

        self.logger.info(f"✅ Selected {tool_type} ({tool_instance.name}) | Score: {score}")

        return tool_type, tool_instance

    def _tool_matches_intent(self, tool_name, intent):
        file_intents = ["create_file", "read_file", "delete_file", "move_file", "list_dir", "write"]
        shell_intents = ["run_command", "install_package", "check_status", "git_op", "execute"]

        if tool_name == "file_tool" and intent in file_intents:
            return True
        if tool_name == "shell_tool" and intent in shell_intents:
            return True

        return False


tool_selector = ToolSelector()
