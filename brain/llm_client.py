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

    async def process_intent(self, event):
        """
        Receives raw text and asks the LLM to return structured JSON.
        """
        task_id = event.data.get("task_id")
        user_text = event.data.get("text")
        
        # We wrap the text in a 'system prompt' to force JSON output
        prompt = self._build_parsing_prompt(user_text)
        
        try:
            response_text = await self._call_ollama(prompt)
            # Try to parse the LLM's string response into a Python dictionary
            intent_data = json.loads(response_text)
            
            # Emit the structured intent back to the bus
            await bus.emit("intent_parsed", {
                "task_id": task_id,
                "intent": intent_data.get("intent"),
                "parameters": intent_data.get("parameters", {}),
                "raw_response": response_text
            }, source="llm_client")
            
        except Exception as e:
            await bus.emit("task_failed", {
                "task_id": task_id, 
                "error": f"LLM Parsing Error: {str(e)}"
            }, source="llm_client")

    async def _call_ollama(self, prompt):
        """Standard async POST request to Ollama's local API."""
        payload = {
            "model": self.model_name,
            "prompt": prompt,
            "stream": False,
            "format": "json"  # Instructs Ollama to strictly return JSON
        }
        
        async with aiohttp.ClientSession() as session:
            async with session.post(self.base_url, json=payload) as resp:
                if resp.status == 200:
                    result = await resp.json()
                    return result.get("response", "{}")
                else:
                    raise Exception(f"Ollama error: {resp.status}")

    def _build_parsing_prompt(self, text):
        return f"""
        Analyze the following user command for an OS Agent: "{text}"
        Return ONLY a JSON object with this structure:
        {{
            "intent": "string_action_name",
            "parameters": {{ "key": "value" }}
        }}
        Example: "Create a folder named backups" -> {{"intent": "file_create", "parameters": {{"type": "directory", "name": "backups"}}}}
        """

# Global instance
llm_client = LLMClient()