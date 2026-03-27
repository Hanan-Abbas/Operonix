import re
from core.event_bus import bus

class StateExtractor:
    async def start(self):
        bus.subscribe("app_classified", self.extract_state)

    async def extract_state(self, event):
        data = event.data
        title = data.get("window_title", "")
        app_type = data.get("app_type")
        
        state = {
            "current_file": None,
            "current_url_domain": None,
            "is_admin": False
        }

        # Example: Extracting filename from VS Code title (usually "filename - folder - VS Code")
        if app_type == "editor":
            parts = title.split(" - ")
            if len(parts) > 0:
                state["current_file"] = parts[0]

        # Example: Extracting domain from Browser title
        elif app_type == "browser":
            if " - " in title:
                state["current_url_domain"] = title.split(" - ")[-1]

        # Merge extracted state into the final context
        data["state"] = state
        
        # ✅ FINALLY: Send the complete context back to the Orchestrator
        await bus.emit("context_snapshot_ready", data, source="state_extractor")
        print(f"🌍 Context Synced: App[{app_type}] File[{state['current_file']}]")

state_extractor = StateExtractor()