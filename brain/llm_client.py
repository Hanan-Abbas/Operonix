import json
import aiohttp
from core.event_bus import bus

class LLMClient:
    def __init__(self, model_name="llama3", base_url="http://localhost:11434/api/generate"):
        self.model_name = model_name
        self.base_url = base_url

    async def start(self):
        """Subscribe to parsing requests from the orchestrator."""
        bus.subscribe("request_intent_parsing", self.process_intent)
        print(f"🧠 LLM Client: Online (Using model: {self.model_name})")

    