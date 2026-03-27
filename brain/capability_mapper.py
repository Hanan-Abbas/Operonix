import logging
from core.event_bus import bus

class CapabilityMapper:
    def __init__(self):
        self.logger = logging.getLogger("CapabilityMapper")
        
        # This maps high-level intents to system capabilities
        # Structure: { intent: (capability_name, priority_tool_hint) }
        self.INTENT_MAP = {
            "create_file": ("file_ops", "file_tool"),
            "delete_file": ("file_ops", "file_tool"),
            "read_file": ("file_ops", "file_tool"),
            "run_terminal": ("command_ops", "shell_tool"),
            "install_package": ("command_ops", "shell_tool"),
            "type_text": ("ui_ops", "ui_tool"),
            "click_element": ("ui_ops", "ui_tool"),
            "search_web": ("web_ops", "api_tool"),
            "open_app": ("system_ops", "shell_tool")
        }

    async def start(self):
        """Listen for parsed intents to map them to capabilities."""
        bus.subscribe("intent_parsed", self.map_intent_to_capability)
        print("🧠 Capability Mapper: Online and listening for intents.")

    async def map_intent_to_capability(self, event):
        """
        Processes a parsed intent and finds the matching system capability.
        """
        task_id = event.data.get("task_id")
        intent = event.data.get("intent")
        extracted_data = event.data.get("data", {})

        if intent in self.INTENT_MAP:
            capability, tool_hint = self.INTENT_MAP[intent]
            
            mapping_result = {
                "task_id": task_id,
                "intent": intent,
                "capability": capability,
                "suggested_tool": tool_hint,
                "args": extracted_data
            }

            self.logger.info(f"📍 Mapped intent '{intent}' to capability '{capability}'")
            
            # Emit to the Planner to turn this capability into a step-by-step plan
            await bus.emit("capability_mapped", mapping_result, source="capability_mapper")
        else:
            self.logger.warning(f"❓ Unknown intent: {intent}. No capability mapping found.")
            await bus.emit("mapping_failed", {
                "task_id": task_id,
                "error": f"No capability found for intent: {intent}"
            }, source="capability_mapper")

# Global instance
capability_mapper = CapabilityMapper()