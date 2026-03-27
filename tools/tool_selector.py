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

    async def select_best_tool(self, intent_data, active_context):
        """
        Analyzes the intent and context to pick the optimal 'Hand'.
        Logic: Try Plugin -> If not available -> Use Tool -> If not possible -> Use UI
        """
        intent = intent_data.get("intent")
        target_app = active_context.get("active_window")
        
        self.logger.info(f"🔍 Selecting tool for intent: {intent} in {target_app}")

        # --- STEP 1: PLUGIN CHECK (API LEVEL) ---
        # Checks if we have a dedicated plugin for the active app (e.g., VS Code, Chrome)
        plugin = plugin_registry.get_plugin_for_app(target_app)
        if plugin and plugin.supports_action(intent):
            self.logger.info(f"✅ Priority 1: Using Plugin for {target_app}")
            return "plugin", plugin

        # --- STEP 2: SPECIALIZED TOOL CHECK (CLI/OS LEVEL) ---
        # If no plugin, check if we have a direct tool for the task
        if self._is_file_operation(intent):
            self.logger.info("✅ Priority 2: Using File Tool (Direct OS access)")
            return "file_tool", tool_registry.get_tool("file_tool")
        
        if self._is_system_command(intent):
            self.logger.info("✅ Priority 2: Using Shell Tool (CLI)")
            return "shell_tool", tool_registry.get_tool("shell_tool")

        # --- STEP 3: UI AUTOMATION FALLBACK (UI LEVEL) ---
        # If all else fails, move the mouse and type
        self.logger.warning("⚠️ Priority 3: No direct tool found. Falling back to UI Automation.")
        return "ui_tool", tool_registry.get_tool("ui_tool")

    