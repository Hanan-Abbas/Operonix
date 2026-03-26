import asyncio
from core.event_bus import bus

class IntentParser:
    def __init__(self):
        # This would eventually be loaded from capabilities/registry.py
        self.supported_intents = [
            "file_create", "file_delete", "file_move", 
            "shell_command", "ui_click", "ui_type", 
            "browser_open", "search_web", "app_launch"
        ]

    async def start(self):
        """Subscribe to the output of the LLM Client."""
        bus.subscribe("intent_parsed", self.validate_and_route)
        print("🧠 Intent Parser: Validator active and monitoring LLM output...")

    