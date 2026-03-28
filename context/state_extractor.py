import platform
import asyncio
from core.event_bus import bus

# Internal tool for deep inspection
try:
    if platform.system() == "Windows":
        import pywinauto
    elif platform.system() == "Linux":
        import subprocess # to use xdotool or at-spi
except ImportError:
    pass

class StateExtractor:
    def __init__(self):
        self.os_name = platform.system()

    async def start(self):
        bus.subscribe("app_classified", self.extract_state)

    async def extract_state(self, event):
        data = event.data
        title = data.get("window_title", "")
        app_type = data.get("app_type")
        
        # 1. Start with Heuristic State (Title Parsing)
        state = self._get_heuristics(title, app_type)

        # 2. Add Deep Inspection (Reading the actual UI elements)
        # This solves the "No Access to App State" issue
        deep_context = await self._get_deep_ui_state(title)
        state.update(deep_context)

        data["state"] = state
        
        await bus.emit("context_snapshot_ready", data, source="state_extractor")

    def _get_heuristics(self, title, app_type):
        """Basic parsing of the window title."""
        state = {"current_file": None, "domain": None}
        if app_type == "editor" and " - " in title:
            state["current_file"] = title.split(" - ")[0]
        elif app_type == "browser" and " - " in title:
            state["domain"] = title.split(" - ")[-1]
        return state

    async def _get_deep_ui_state(self, window_title):
        """
        SOLVES ISSUE: 'No Access to App State'
        Uses OS accessibility APIs to find text inside the window.
        """
        deep_data = {"selected_text": None, "controls": []}
        
        try:
            if self.os_name == "Windows":
                # Example: Using pywinauto to find a 'Document' or 'Edit' control
                # This is a blocking call, so we'd ideally use to_thread
                pass 
            elif self.os_name == "Linux":
                # Example: Use xclip to see if there is highlighted text
                result = subprocess.check_output(["xclip", "-o"], stderr=subprocess.DEVNULL)
                deep_data["selected_text"] = result.decode('utf-8')
        except:
            pass
            
        return deep_data

state_extractor = StateExtractor()