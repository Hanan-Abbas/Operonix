import json
import aiohttp
import logging
from core.event_bus import bus
from core.config import settings

class LLMClient:
    def __init__(self):
        self.logger = logging.getLogger("LLMClient")
        self.ollama_url = "http://localhost:11434/api/generate"

    async def start(self):
        """Listen for any brain-related requests."""
        bus.subscribe("request_intent_parsing", self.process_intent)
        bus.subscribe("request_reasoning", self.process_reasoning)
        print("🧠 LLM Client: Online & Multi-Provider Capable")

    # 🔄 UPGRADE 5: Explicit Role Separation
    async def generate(self, prompt: str, use_json: bool = False):
        """Primary code generator using DeepSeek."""
        return await self.ask(prompt, provider="deepseek", use_json=use_json)

    async def critique(self, prompt: str, use_json: bool = True):
        """Strict code reviewer and auditor using Gemini."""
        return await self.ask(prompt, provider="gemini", use_json=use_json)

    # 🔄 UPGRADE 1 & 2: Fallback System + Retry Logic
    async def ask(self, prompt: str, provider: str = "local", use_json: bool = True):
        """
        Generic method to ask an LLM anything. 
        Tries the primary provider with retries, falling back to Ollama on failure.
        """
        try:
            if provider == "deepseek":
                # Try DeepSeek with 2 retries
                result = await self._retry(self._call_deepseek, prompt, use_json, retries=2)
                if result:
                    return result
                raise Exception("DeepSeek failed after retries")

            elif provider == "gemini":
                # Try Gemini with 2 retries
                result = await self._retry(self._call_gemini, prompt, use_json, retries=2)
                if result:
                    return result
                raise Exception("Gemini failed after retries")

            else:
                return await self._call_ollama(prompt, use_json)

        except Exception as e:
            self.logger.warning(f"🚨 Primary provider '{provider}' failed: {e}. Falling back to local Ollama...")
            # Ultimate safety net: hit local Llama3
            return await self._call_ollama(prompt, use_json)

    async def _retry(self, func, *args, retries=2):
        """Helper to retry async API calls."""
        for i in range(retries):
            try:
                result = await func(*args)
                if result:
                    return result
            except Exception as e:
                self.logger.warning(f"Attempt {i+1} failed with error: {e}")
        return None

    # 🔄 UPGRADE 3: Safe JSON parsing
    def _safe_json(self, text):
        """Prevents crashes if the model returns invalid JSON markdown."""
        try:
            # Clean up potential markdown code blocks if the model ignored instructions
            cleaned = text.strip()
            if cleaned.startswith("```json"):
                cleaned = cleaned.split("```json")[1].split("```")[0].strip()
            elif cleaned.startswith("```"):
                cleaned = cleaned.split("```")[1].split("```")[0].strip()
                
            return json.loads(cleaned)
        except Exception:
            self.logger.warning("Failed to parse JSON. Returning raw text wrapped in dict.")
            return {"raw": text}

    # --- 🦙 PROVIDER 1: OLLAMA (Local Llama3) ---
    async def _call_ollama(self, prompt, use_json):
        payload = {"model": "llama3", "prompt": prompt, "stream": False}
        if use_json:
            payload["format"] = "json"

        async with aiohttp.ClientSession() as session:
            async with session.post(self.ollama_url, json=payload) as resp:
                if resp.status == 200:
                    result = await resp.json()
                    response_text = result.get("response", "{}")
                    return self._safe_json(response_text) if use_json else response_text
                return None

    # --- 🐳 PROVIDER 2: DEEPSEEK ---
    async def _call_deepseek(self, prompt, use_json):
        url = "https://api.deepseek.com/chat/completions"
        headers = {
            "Authorization": f"Bearer {settings.DEEPSEEK_API_KEY}",
            "Content-Type": "application/json"
        }
        payload = {
            "model": "deepseek-chat",
            "messages": [{"role": "user", "content": prompt}],
            "temperature": 0.1
        }
        if use_json:
            payload["response_format"] = {"type": "json_object"}

        async with aiohttp.ClientSession() as session:
            async with session.post(url, headers=headers, json=payload) as resp:
                if resp.status == 200:
                    result = await resp.json()
                    content = result['choices'][0]['message']['content']
                    return self._safe_json(content) if use_json else content
                return None

    # --- ✨ PROVIDER 3: GEMINI ---
    async def _call_gemini(self, prompt, use_json):
        # 🔄 UPGRADE 4: Using gemini-2.5-flash for speed & efficiency
        url = f"https://generativelanguage.googleapis.com/v1beta/models/gemini-2.5-flash:generateContent?key={settings.GEMINI_API_KEY}"
        headers = {"Content-Type": "application/json"}
        payload = {
            "contents": [{"parts": [{"text": prompt}]}],
            "generationConfig": {"temperature": 0.2}
        }
        if use_json:
            payload["generationConfig"]["responseMimeType"] = "application/json"

        async with aiohttp.ClientSession() as session:
            async with session.post(url, headers=headers, json=payload) as resp:
                if resp.status == 200:
                    result = await resp.json()
                    content = result['candidates'][0]['content']['parts'][0]['text']
                    return self._safe_json(content) if use_json else content
                return None

    # --- EVENT BUS HANDLERS ---
    async def process_intent(self, event):
        task_id = event.data.get("task_id")
        user_text = event.data.get("text")
        
        prompt = self._build_parsing_prompt(user_text)
        intent_data = await self.ask(prompt, provider="local", use_json=True)
        
        if isinstance(intent_data, dict) and "intent" in intent_data:
            params = intent_data.get("parameters") or {}
            await bus.emit(
                "intent_parsed",
                {"task_id": task_id, "intent": intent_data.get("intent"), "parameters": params},
                source="llm_client",
            )
        else:
            await bus.emit("task_failed", {"task_id": task_id, "error": "LLM failed to parse intent."})

    async def process_reasoning(self, event):
        task_id = event.data.get("task_id")
        prompt = event.data.get("prompt")
        
        # Using our explicit role method!
        result = await self.generate(prompt)
        await bus.emit("reasoning_completed", {"task_id": task_id, "response": result}, source="llm_client")

    def _build_parsing_prompt(self, text):
        return f'Analyze the user command: "{text}". Return JSON: {{ "intent": "<capability>", "parameters": {{ ... }} }}'

# Global instance
llm_client = LLMClient()