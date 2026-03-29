import json
import aiohttp
import logging
from core.event_bus import bus

class LLMClient:
    def __init__(self, model_name="llama3", base_url="http://localhost:11434/api/generate"):
        self.model_name = model_name
        self.base_url = base_url
        self.logger = logging.getLogger("LLMClient")

    async def start(self):
        """Listen for any brain-related requests."""
        bus.subscribe("request_intent_parsing", self.process_intent)
        # We also listen for general 'reasoning' requests from the Planner
        bus.subscribe("request_reasoning", self.process_reasoning)
        print(f"🧠 LLM Client: Online (Model: {self.model_name})")

    async def ask(self, prompt, use_json=True):
        """
        Generic method to ask the LLM anything. 
        Used by Planner for coding steps, or Validator for safety checks.
        """
        payload = {
            "model": self.model_name,
            "prompt": prompt,
            "stream": False
        }
        if use_json:
            payload["format"] = "json"

        try:
            async with aiohttp.ClientSession() as session:
                async with session.post(self.base_url, json=payload) as resp:
                    if resp.status == 200:
                        result = await resp.json()
                        response_text = result.get("response", "{}")
                        return json.loads(response_text) if use_json else response_text
                    else:
                        self.logger.error(f"Ollama API Error: {resp.status}")
                        return None
        except Exception as e:
            self.logger.error(f"LLM Connection Error: {e}")
            return None

    async def process_intent(self, event):
        """Parses raw user text into an Intent + Parameters."""
        task_id = event.data.get("task_id")
        user_text = event.data.get("text")
        
        prompt = self._build_parsing_prompt(user_text)
        intent_data = await self.ask(prompt, use_json=True)
        
        if intent_data:
            params = intent_data.get("parameters") or intent_data.get("args") or {}
            await bus.emit(
                "intent_parsed",
                {
                    "task_id": task_id,
                    "intent": intent_data.get("intent"),
                    "parameters": params,
                    "data": params,
                },
                source="llm_client",
            )
        else:
            await bus.emit("task_failed", {"task_id": task_id, "error": "LLM failed to parse intent."})

    async def process_reasoning(self, event):
        """Used by the Planner to break down complex tasks like Coding."""
        task_id = event.data.get("task_id")
        prompt = event.data.get("prompt")
        
        result = await self.ask(prompt, use_json=True)
        await bus.emit("reasoning_completed", {"task_id": task_id, "response": result}, source="llm_client")

    def _build_parsing_prompt(self, text):
        try:
            from capabilities.registry import capability_registry

            names = capability_registry.list_registered()
            catalog = ", ".join(names) if names else "write_file, read_file, run_command, type_text, click, open_url, search_web"
        except Exception:
            catalog = "write_file, read_file, run_command, type_text, click, open_url, search_web"

        return f"""
        Analyze the user command: "{text}"
        Return JSON: {{ "intent": "<one_capability_name>", "parameters": {{ ... }} }}
        Parameters must match the intent (e.g. write_file: path, content; run_command: command; search_web: query).
        Registered capability names include: {catalog}
        """

# Global instance
llm_client = LLMClient()