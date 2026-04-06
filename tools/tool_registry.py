import logging
import importlib
from core.config import settings

class ToolRegistry:
    def __init__(self):
        self.logger = logging.getLogger("ToolRegistry")
        # Start with an empty dictionary. Zero hardcoded tools!
        self._tools = {}

    def get_tool(self, tool_name: str):
        """Retrieve a tool by its string name."""
        return self._tools.get(tool_name)

    def list_tools(self):
        """Returns a list of all registered tool names."""
        return list(self._tools.keys())
    
    def get_all_tools(self):
        """Returns the raw dictionary of tools (needed for Executor scans)."""
        return self._tools

    def register_tool(self, name: str, tool_instance):
        """Allows for dynamic tool addition (important for the Plugin AI system)."""
        self._tools[name] = tool_instance
        self.logger.info(f"Tool '{name}' registered successfully.")

    def auto_load_tools(self):
        """
        🟢 ZERO HARDCODING: Dynamically loads the core tools.
        This pulls the tools list from your config or attempts dynamic imports.
        """
        # We fetch the list of core tools from settings. 
        # If your settings file doesn't have it, we fallback to these 4 defaults.
        core_tools = getattr(settings, "CORE_TOOLS", ["file_tool", "shell_tool", "ui_tool", "api_tool"])
        
        for tool_name in core_tools:
            try:
                # This programmatically does 'from tools.file_tool import file_tool'
                module = importlib.import_module(f"tools.{tool_name}")
                tool_instance = getattr(module, tool_name, None)
                
                if tool_instance:
                    self.register_tool(tool_name, tool_instance)
                else:
                    self.logger.warning(f"Could not find instance '{tool_name}' in module 'tools.{tool_name}'")
                    
            except ImportError as e:
                self.logger.error(f"Failed to auto-load core tool '{tool_name}': {e}")


# Global instance for the Executor to use
tool_registry = ToolRegistry()

# 🟢 Auto-load the tools immediately on initialization
tool_registry.auto_load_tools()