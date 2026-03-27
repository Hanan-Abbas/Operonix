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

    