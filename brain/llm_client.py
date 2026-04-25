import json
import aiohttp
import logging
import re
from core.event_bus import bus
from core.config import settings
from brain.intent_matcher import match_intent_local


class LLMClient:
    def __init__(self):
        self.logger = logging.getLogger("LLMClient")
        self.ollama_url = "http://localhost:11434/api/generate"
        self.ollama_embed_url = "http://localhost:11434/api/embed"

    async def start(self):
        """Listen for any brain-related requests."""
        bus.subscribe("request_intent_parsing", self.process_intent)
        bus.subscribe("request_reasoning", self.process_reasoning)
        print("🧠 LLM Client: Online & Multi-Provider Capable")

    async def generate(self, prompt: str, use_json: bool = False):
        """Primary code generator using OpenRouter (DeepSeek R1)."""
        return await self.ask(prompt, provider="deepseek", use_json=use_json)

    async def critique(self, prompt: str, use_json: bool = True):
        """Strict code reviewer and auditor using Gemini."""
        return await self.ask(prompt, provider="gemini", use_json=use_json)

    async def ask(self, prompt: str, provider: str = "local", use_json: bool = True):
        """
        Generic method to ask an LLM anything.
        Tries the primary provider with retries, falling back to Ollama on failure.

        BUG FIX: The original code raised an exception after retries fail, which
        was caught by the outer except and triggered a redundant Ollama fallback
        log message even when Ollama itself was the provider. Now each branch
        returns the Ollama fallback directly and cleanly.
        """
        try:
            if provider == "deepseek":
                result = await self._retry(self._call_openrouter, prompt, use_json, retries=2)
                if result:
                    return result
                # BUG FIX: Don't raise here — fall through to Ollama cleanly
                self.logger.warning("🚨 OpenRouter/DeepSeek failed after retries. Falling back to local Ollama...")
                return await self._call_ollama(prompt, use_json)

            elif provider == "gemini":
                result = await self._retry(self._call_gemini, prompt, use_json, retries=2)
                if result:
                    return result
                self.logger.warning("🚨 Gemini failed after retries. Falling back to local Ollama...")
                return await self._call_ollama(prompt, use_json)

            else:
                return await self._call_ollama(prompt, use_json)

        except Exception as e:
            self.logger.warning(f"🚨 Provider '{provider}' encountered unexpected error: {e}. Falling back to local Ollama...")
            return await self._call_ollama(prompt, use_json)

    async def _retry(self, func, *args, retries=2):
        """Helper to retry async API calls."""
        for i in range(retries):
            try:
                result = await func(*args)
                if result:
                    return result
            except Exception as e:
                self.logger.warning(f"Attempt {i + 1} failed: {e}")
        return None

    async def get_embedding(self, text: str) -> list[float]:
        """Generates a vector embedding for a given text using Ollama's all-minilm."""
        payload = {
            "model": "all-minilm",
            "input": text
        }
        try:
            async with aiohttp.ClientSession() as session:
                async with session.post(self.ollama_embed_url, json=payload) as resp:
                    if resp.status == 200:
                        data = await resp.json()
                        vectors = data.get("embeddings", [])
                        return vectors[0] if vectors else []
                    else:
                        self.logger.error(f"Ollama embedding failed with status: {resp.status}")
                        return []
        except Exception as e:
            self.logger.error(f"Error connecting to Ollama for embeddings: {e}")
            return []

    def _safe_json(self, text):
        """Prevents crashes if the model returns invalid JSON or markdown fences."""
        try:
            cleaned = text.strip()
            if cleaned.startswith("```json"):
                cleaned = cleaned.split("```json")[1].split("```")[0].strip()
            elif cleaned.startswith("```"):
                cleaned = cleaned.split("```")[1].split("```")[0].strip()
            return json.loads(cleaned)
        except Exception:
            match = re.search(r"\{[\s\S]*\}", text or "")
            if match:
                try:
                    return json.loads(match.group(0))
                except Exception:
                    pass
            self.logger.warning("Failed to parse JSON. Returning raw text wrapped in dict.")
            return {"raw": text}

    # --- 🦙 PROVIDER 1: OLLAMA (Local Llama3) ---
    async def _call_ollama(self, prompt, use_json):
        """
        BUG FIX: Added explicit timeout (aiohttp.ClientTimeout) so Ollama
        calls don't hang forever if the local server is unresponsive.
        Also added non-200 status logging.
        """
        payload = {"model": settings.OLLAMA_MODEL, "prompt": prompt, "stream": False}
        if use_json:
            payload["format"] = "json"

        timeout = aiohttp.ClientTimeout(total=settings.OLLAMA_TIMEOUT)
        try:
            async with aiohttp.ClientSession(timeout=timeout) as session:
                async with session.post(self.ollama_url, json=payload) as resp:
                    if resp.status == 200:
                        result = await resp.json()
                        response_text = result.get("response", "{}")
                        return self._safe_json(response_text) if use_json else response_text
                    else:
                        self.logger.error(f"Ollama returned non-200 status: {resp.status}")
                        return None
        except Exception as e:
            self.logger.error(f"Ollama call failed: {e}")
            return None

    # --- 🌐 PROVIDER 2: OPENROUTER (DeepSeek R1 Distill Qwen 14B) ---
    async def _call_openrouter(self, prompt, use_json):
        """
        CHANGED: Replaced direct DeepSeek API with OpenRouter.
        - URL: https://openrouter.ai/api/v1/chat/completions
        - Model: deepseek/deepseek-r1-distill-qwen-14b
        - Auth: Bearer OPENROUTER_API_KEY from config/env
        - Added timeout to avoid hanging on slow responses.

        NOTE: deepseek-r1-distill-qwen-14b is a reasoning model that wraps
        its answer in <think>...</think> tags. The _strip_think_tags helper
        removes those before JSON parsing so _safe_json doesn't choke.

        NOTE: json_object response_format is NOT supported on this model via
        OpenRouter — we rely on prompt instructions + _safe_json instead.
        """
        url = "https://openrouter.ai/api/v1/chat/completions"
        headers = {
            "Authorization": f"Bearer {settings.OPENROUTER_API_KEY}",
            "Content-Type": "application/json",
            # OpenRouter recommends these headers for tracking/ranking
            "HTTP-Referer": "https://github.com/your-project",
            "X-Title": "Operonix AI OS Agent",
        }
        payload = {
            "model": "deepseek/deepseek-r1-distill-qwen-14b",
            "messages": [{"role": "user", "content": prompt}],
            "temperature": 0.1,
        }
        # NOTE: Do NOT set response_format json_object — not supported on this model

        timeout = aiohttp.ClientTimeout(total=60)
        try:
            async with aiohttp.ClientSession(timeout=timeout) as session:
                async with session.post(url, headers=headers, json=payload) as resp:
                    if resp.status == 200:
                        result = await resp.json()
                        content = result["choices"][0]["message"]["content"]
                        # Strip <think>...</think> reasoning block before parsing
                        content = self._strip_think_tags(content)
                        return self._safe_json(content) if use_json else content
                    else:
                        body = await resp.text()
                        self.logger.error(f"OpenRouter returned {resp.status}: {body[:200]}")
                        return None
        except Exception as e:
            self.logger.error(f"OpenRouter call failed: {e}")
            return None

    def _strip_think_tags(self, text: str) -> str:
        """
        DeepSeek R1 reasoning models emit <think>...</think> blocks before
        the actual answer. Strip them so JSON parsing is not broken.
        """
        return re.sub(r"<think>[\s\S]*?</think>", "", text or "").strip()

    # --- ✨ PROVIDER 3: GEMINI ---
    async def _call_gemini(self, prompt, use_json):
        url = (
            f"https://generativelanguage.googleapis.com/v1beta/models/"
            f"gemini-2.5-flash:generateContent?key={settings.GEMINI_API_KEY}"
        )
        headers = {"Content-Type": "application/json"}
        payload = {
            "contents": [{"parts": [{"text": prompt}]}],
            "generationConfig": {"temperature": 0.2},
        }
        if use_json:
            payload["generationConfig"]["responseMimeType"] = "application/json"

        timeout = aiohttp.ClientTimeout(total=30)
        try:
            async with aiohttp.ClientSession(timeout=timeout) as session:
                async with session.post(url, headers=headers, json=payload) as resp:
                    if resp.status == 200:
                        result = await resp.json()
                        content = result["candidates"][0]["content"]["parts"][0]["text"]
                        return self._safe_json(content) if use_json else content
                    else:
                        body = await resp.text()
                        self.logger.error(f"Gemini returned {resp.status}: {body[:200]}")
                        return None
        except Exception as e:
            self.logger.error(f"Gemini call failed: {e}")
            return None

    # --- EVENT BUS HANDLERS ---
    async def process_intent(self, event):
        task_id = event.data.get("task_id")
        user_text = event.data.get("text")
        stt_meta = event.data.get("stt") or {}

        stt_conf = stt_meta.get("confidence")
        if isinstance(stt_conf, (int, float)) and float(stt_conf) < 0.25:
            safe_intent = self._choose_safe_text_intent()
            if safe_intent:
                await bus.emit(
                    "intent_parsed",
                    {"task_id": task_id, "intent": safe_intent, "parameters": {"text": user_text, "stt": stt_meta}},
                    source="llm_client",
                )
                return

        try:
            prompt = self._build_parsing_prompt(user_text)
            raw_intent = await self._ask_intent_with_provider_fallback(prompt)
            result = await self._normalize_intent_output(task_id, user_text, raw_intent)

            if result:
                await bus.emit("intent_parsed", result, source="llm_client")
            else:
                fallback = self._fallback_intent_from_user_text(user_text)
                if fallback:
                    await bus.emit(
                        "intent_parsed",
                        {"task_id": task_id, **fallback},
                        source="llm_client",
                    )
                else:
                    await bus.emit("task_failed", {"task_id": task_id, "error": "LLM failed to parse intent."})
        except Exception as e:
            self.logger.error(f"process_intent error for task {task_id}: {e}")
            await bus.emit("task_failed", {"task_id": task_id, "error": str(e)})

    def _choose_safe_text_intent(self):
        intents = set(self._get_registered_intents())
        for preferred in ("generate_text", "summarize_text", "correct_grammar", "translate_text"):
            if preferred in intents:
                return preferred
        return None

    async def process_reasoning(self, event):
        task_id = event.data.get("task_id")
        prompt = event.data.get("prompt")
        result = await self.generate(prompt)
        await bus.emit("reasoning_completed", {"task_id": task_id, "response": result}, source="llm_client")

    def _build_parsing_prompt(self, text):
        allowed_intents = self._get_registered_intents()
        return (
            f'Analyze the user command: "{text}". '
            'Return ONLY strict JSON with this exact schema: '
            '{ "intent": "<one_registered_capability_name>", "parameters": { ... } }. '
            f"Use one of these intents when possible: {json.dumps(allowed_intents)}. "
            "Do not include markdown, <think> blocks, or explanation. Output JSON only."
        )

    async def _ask_intent_with_provider_fallback(self, prompt):
        if settings.OPENROUTER_API_KEY:
            result = await self.ask(prompt, provider="deepseek", use_json=True)
            if result and "raw" not in result:
                return result

        if settings.GEMINI_API_KEY:
            result = await self.ask(prompt, provider="gemini", use_json=True)
            if result and "raw" not in result:
                return result

        return await self.ask(prompt, provider="local", use_json=True)

    def _get_registered_intents(self):
        try:
            from capabilities.registry import capability_registry
            return capability_registry.get_all_names()
        except Exception:
            return []

    def _coerce_parsed_intent(self, intent_data):
        if not isinstance(intent_data, dict):
            return None

        intent = (
            intent_data.get("intent")
            or intent_data.get("capability")
            or intent_data.get("action")
        )
        params = (
            intent_data.get("parameters")
            if isinstance(intent_data.get("parameters"), dict)
            else intent_data.get("args")
            if isinstance(intent_data.get("args"), dict)
            else {}
        )

        if not intent:
            payload = intent_data.get("result") or intent_data.get("data")
            if isinstance(payload, dict):
                intent = (
                    payload.get("intent")
                    or payload.get("capability")
                    or payload.get("action")
                )
                if isinstance(payload.get("parameters"), dict):
                    params = payload.get("parameters")
                elif isinstance(payload.get("args"), dict):
                    params = payload.get("args")

        if not intent:
            return None

        return {"intent": str(intent).strip(), "parameters": params}

    async def _repair_intent_with_llm(self, user_text, intent_data):
        allowed_intents = self._get_registered_intents()
        repair_prompt = (
            "You are repairing malformed intent-parser output.\n"
            f"User text: {user_text}\n"
            f"Allowed intents: {json.dumps(allowed_intents)}\n"
            f"Raw parser output: {json.dumps(intent_data)}\n"
            'Return ONLY JSON: {"intent": "<allowed_intent>", "parameters": { ... }}\n'
            "Choose the best matching intent from the allowed list."
        )
        return await self._ask_intent_with_provider_fallback(repair_prompt)

    def _fallback_intent_from_user_text(self, user_text):
        text = (user_text or "").strip()
        if not text:
            return None
        return {"intent": text, "parameters": {"text": text}}

    async def _semantic_resolve_registered_intent(self, candidate_text):
        if not candidate_text:
            return None

        threshold = float(getattr(settings, "INTENT_MATCH_MIN_CONFIDENCE", 0.30))
        best_intent, best_score = match_intent_local(
            candidate_text=candidate_text,
            allowed_intents=self._get_registered_intents(),
            threshold=threshold,
        )
        if best_intent:
            self.logger.info(
                "🔎 Local intent fallback matched '%s' -> '%s' (score=%.2f)",
                candidate_text, best_intent, best_score,
            )
            return best_intent
        return None

    async def _normalize_intent_output(self, task_id, user_text, intent_data):
        parsed = self._coerce_parsed_intent(intent_data)
        if not parsed:
            repaired = await self._repair_intent_with_llm(user_text, intent_data)
            parsed = self._coerce_parsed_intent(repaired)
            if not parsed:
                resolved = await self._semantic_resolve_registered_intent(user_text)
                if not resolved:
                    return None
                return {"task_id": task_id, "intent": resolved, "parameters": {"text": user_text}}

        intent_name = parsed["intent"]
        params = parsed["parameters"] if isinstance(parsed["parameters"], dict) else {}

        allowed = set(self._get_registered_intents())
        if allowed and intent_name not in allowed:
            resolved = await self._semantic_resolve_registered_intent(intent_name)
            if not resolved:
                resolved = await self._semantic_resolve_registered_intent(user_text)
            if not resolved:
                repaired = await self._repair_intent_with_llm(
                    user_text, {"intent": intent_name, "parameters": params}
                )
                repaired_parsed = self._coerce_parsed_intent(repaired)
                if repaired_parsed:
                    repaired_intent = repaired_parsed.get("intent")
                    if repaired_intent in allowed:
                        intent_name = repaired_intent
                        params = repaired_parsed.get("parameters") or params
                    else:
                        resolved = await self._semantic_resolve_registered_intent(repaired_intent)
                        if resolved:
                            intent_name = resolved
                        else:
                            return None
                else:
                    return None
            else:
                intent_name = resolved

        return {"task_id": task_id, "intent": intent_name, "parameters": params}


# Global instance
llm_client = LLMClient()