import logging
from tools.tool_registry import tool_registry
from plugins.plugin_registry import plugin_registry # Reference to your plugin system

class ToolSelector:
    def __init__(self):
        self.logger = logging.getLogger("ToolSelector")
        
        # Scoring System: Higher is better (API/Plugin > CLI > UI)
        self.STRATEGY_PRIORITY = {
            "PLUGIN": 100,      # Native app integration (Fastest/Safest)
            "FILE_TOOL": 90,    # Direct OS filesystem access
            "SHELL_TOOL": 80,   # Terminal execution
            "UI_TOOL": 60       # Visual automation (PyAutoGUI - Last resort)
        }

    