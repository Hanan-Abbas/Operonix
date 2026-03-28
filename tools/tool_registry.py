import logging
from tools.file_tool import file_tool
from tools.shell_tool import shell_tool
from tools.ui_tool import ui_tool

class ToolRegistry:
    def __init__(self):
        self.logger = logging.getLogger("ToolRegistry")
        # Initialize the registry with our created tools
        self._tools = {
            "file_tool": file_tool,
            "shell_tool": shell_tool,
            "ui_tool": ui_tool,
            "api_tool": api_tool
        }

    def get_tool(self, tool_name: str):
        """Retrieve a tool by its string name."""
        return self._tools.get(tool_name)

    def list_tools(self):
        """Returns a list of all registered tool names."""
        return list(self._tools.keys())

    def register_tool(self, name: str, tool_instance):
        """Allows for dynamic tool addition (important for the Plugin AI system)."""
        self._tools[name] = tool_instance
        self.logger.info(f"Tool '{name}' registered successfully.")

# Global instance for the Executor to use
tool_registry = ToolRegistry()