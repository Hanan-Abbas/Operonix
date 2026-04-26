from __future__ import annotations

import json
import logging
import re

import aiohttp

from core.config import settings
from core.event_bus import bus
from brain.intent_matcher import match_intent_local


class LLMClient:
    def __init__(self):
        self.logger = logging.getLogger("LLMClient")
        self.ollama_url = f"{settings.OLLAMA_BASE_URL}/api/generate"
        self.ollama_embed_url = f"{settings.OLLAMA_BASE_URL}/api/embed"

    async def start(self):
        """Listen for any brain-related requests."""
        bus.subscribe("request_intent_parsing", self.process_intent)
        bus.subscribe("request_reasoning", self.process_reasoning)
        self.logger.info("🧠 LLM Client: Online & Multi-Provider Capable")

    # ── Public generation / critique helpers ──────────────────────────────────

    async def generate(self, prompt: str, use_json: bool = False):
        """Primary code generator.

        Uses OpenRouter if a key + model are configured, otherwise local Ollama.
        """
        if settings.OPENROUTER_API_KEY and settings.OPENROUTER_MODEL:
            return await self.ask(prompt, provider="openrouter", use_json=use_json)
        return await self.ask(prompt, provider="local", use_json=use_json)

    async def critique(self, prompt: str, use_json: bool = True):
        """Strict code reviewer.

        Uses Gemini if a key is configured, otherwise local Ollama.
        """
        if settings.GEMINI_API_KEY:
            return await self.ask(prompt, provider="gemini", use_json=use_json)
        return await self.ask(prompt, provider="local", use_json=use_json)

    async def ask(self, prompt: str, provider: str = "local", use_json: bool = True):
        """
        Generic method to ask an LLM anything.

        Priority waterfall: openrouter → gemini → local (Ollama).
        Each cloud branch falls back to local on failure so the system
        always gets a response as long as Ollama is running.
        """
        try:
            if provider == "openrouter":
                result = await self._retry(self._call_openrouter, prompt, use_json, retries=2)
                if result:
                    return result
                self.logger.warning(
                    "🚨 OpenRouter failed after retries. Falling back to local Ollama..."
                )
                return await self._call_ollama(prompt, use_json)

            elif provider == "deepseek":
                # Legacy alias — route to openrouter with the same fallback.
                return await self.ask(prompt, provider="openrouter", use_json=use_json)

            elif provider == "gemini":
                result = await self._retry(self._call_gemini, prompt, use_json, retries=2)
                if result:
                    return result
                self.logger.warning(
                    "🚨 Gemini failed after retries. Falling back to local Ollama..."
                )
                return await self._call_ollama(prompt, use_json)

            else:
                return await self._call_ollama(prompt, use_json)

        except Exception as exc:
            self.logger.warning(
                "🚨 Provider '%s' encountered unexpected error: %s. "
                "Falling back to local Ollama...",
                provider, exc,
            )
            return await self._call_ollama(prompt, use_json)

    # ── Internal retry helper ─────────────────────────────────────────────────

    async def _retry(self, func, *args, retries: int = 2):
        """Retry an async API call up to `retries` times."""
        for attempt in range(retries):
            try:
                result = await func(*args)
                if result:
                    return result
            except Exception as exc:
                self.logger.warning("Attempt %d/%d failed: %s", attempt + 1, retries, exc)
        return None

    # ── Embedding ─────────────────────────────────────────────────────────────

    async def get_embedding(self, text: str) -> list[float]:
        """Generate a vector embedding using Ollama's configured embed model."""
        payload = {
            "model": settings.OLLAMA_EMBED_MODEL,
            "input": text,
        }
        try:
            async with aiohttp.ClientSession() as session:
                async with session.post(self.ollama_embed_url, json=payload) as resp:
                    if resp.status == 200:
                        data = await resp.json()
                        vectors = data.get("embeddings", [])
                        return vectors[0] if vectors else []
                    else:
                        self.logger.error(
                            "Ollama embedding failed with status %d", resp.status
                        )
                        return []
        except Exception as exc:
            self.logger.error("Error connecting to Ollama for embeddings: %s", exc)
            return []

    # ── JSON safety ───────────────────────────────────────────────────────────

    def _safe_json(self, text):
        """Parse JSON from model output robustly; strips markdown fences."""
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

    # ── Provider 1: Ollama (local) ────────────────────────────────────────────

    async def _call_ollama(self, prompt: str, use_json: bool):
        """
        Call local Ollama.

        Model name comes from settings.OLLAMA_MODEL (default "llama3") — set
        this in your .env to match whatever model you have pulled locally,
        e.g. OLLAMA_MODEL=llama3.2  or  OLLAMA_MODEL=mistral.

        FIX: base URL now read from settings.OLLAMA_BASE_URL so it is never
        hardcoded.  Explicit timeout prevents hangs when Ollama is slow.
        """
        payload: dict = {
            "model": settings.OLLAMA_MODEL,
            "prompt": prompt,
            "stream": False,
        }
        if use_json:
            payload["format"] = "json"

        timeout = aiohttp.ClientTimeout(total=int(getattr(settings, "OLLAMA_TIMEOUT", 60)))
        try:
            async with aiohttp.ClientSession(timeout=timeout) as session:
                async with session.post(self.ollama_url, json=payload) as resp:
                    if resp.status == 200:
                        result = await resp.json()
                        response_text = result.get("response", "{}")
                        return self._safe_json(response_text) if use_json else response_text
                    else:
                        body = await resp.text()
                        self.logger.error(
                            "Ollama returned non-200 status %d: %s",
                            resp.status, body[:300],
                        )
                        return None
        except Exception as exc:
            self.logger.error("Ollama call failed: %s", exc)
            return None

    # ── Provider 2: OpenRouter ────────────────────────────────────────────────

    async def _call_openrouter(self, prompt: str, use_json: bool):
        """
        Call OpenRouter with whatever model is configured in settings.

        FIX (root cause of 404 errors): The model slug was previously
        hardcoded to "deepseek/deepseek-r1-distill-qwen-14b" which is no
        longer available on OpenRouter.  The slug is now read from
        settings.OPENROUTER_MODEL so it can be changed in .env without
        touching code.

        Recommended free/low-cost models on OpenRouter (April 2026):
          • meta-llama/llama-3.1-8b-instruct:free
          • mistralai/mistral-7b-instruct:free
          • google/gemma-3-27b-it:free
          • deepseek/deepseek-chat-v3-0324:free   (DeepSeek V3, free tier)

        Set in .env:  OPENROUTER_MODEL=meta-llama/llama-3.1-8b-instruct:free
        """
        if not settings.OPENROUTER_API_KEY:
            return None

        model = getattr(settings, "OPENROUTER_MODEL", "").strip()
        if not model:
            self.logger.warning(
                "OPENROUTER_MODEL is not set — skipping OpenRouter call. "
                "Add OPENROUTER_MODEL=<slug> to your .env file."
            )
            return None

        url = "https://openrouter.ai/api/v1/chat/completions"
        headers = {
            "Authorization": f"Bearer {settings.OPENROUTER_API_KEY}",
            "Content-Type": "application/json",
            "HTTP-Referer": "https://github.com/Operonix/Operonix",
            "X-Title": "Operonix AI OS Agent",
        }
        payload: dict = {
            "model": model,
            "messages": [{"role": "user", "content": prompt}],
            "temperature": 0.1,
        }
        # Only request JSON response_format for models that support it.
        # Reasoning models (e.g. DeepSeek R1 variants) do NOT support it.
        if use_json and not self._is_reasoning_model(model):
            payload["response_format"] = {"type": "json_object"}

        timeout = aiohttp.ClientTimeout(total=60)
        try:
            async with aiohttp.ClientSession(timeout=timeout) as session:
                async with session.post(url, headers=headers, json=payload) as resp:
                    if resp.status == 200:
                        result = await resp.json()
                        content = result["choices"][0]["message"]["content"]
                        content = self._strip_think_tags(content)
                        return self._safe_json(content) if use_json else content
                    else:
                        body = await resp.text()
                        self.logger.error(
                            "OpenRouter returned %d: %s", resp.status, body[:300]
                        )
                        return None
        except Exception as exc:
            self.logger.error("OpenRouter call failed: %s", exc)
            return None

    @staticmethod
    def _is_reasoning_model(model_slug: str) -> bool:
        """Detect reasoning models that wrap output in <think> tags."""
        reasoning_keywords = ("r1", "o1", "o3", "deepseek-r", "qwq", "thinking")
        slug_lower = model_slug.lower()
        return any(kw in slug_lower for kw in reasoning_keywords)

    def _strip_think_tags(self, text: str) -> str:
        """Strip <think>…</think> reasoning blocks emitted by some models."""
        return re.sub(r"<think>[\s\S]*?</think>", "", text or "").strip()

    # ── Provider 3: Gemini ────────────────────────────────────────────────────

    async def _call_gemini(self, prompt: str, use_json: bool):
        """Call Google Gemini via the REST API."""
        if not settings.GEMINI_API_KEY:
            return None

        model = getattr(settings, "GEMINI_MODEL", "gemini-2.0-flash")
        url = (
            f"https://generativelanguage.googleapis.com/v1beta/models/"
            f"{model}:generateContent?key={settings.GEMINI_API_KEY}"
        )
        headers = {"Content-Type": "application/json"}
        payload: dict = {
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
                        content = (
                            result["candidates"][0]["content"]["parts"][0]["text"]
                        )
                        return self._safe_json(content) if use_json else content
                    else:
                        body = await resp.text()
                        self.logger.error(
                            "Gemini returned %d: %s", resp.status, body[:200]
                        )
                        return None
        except Exception as exc:
            self.logger.error("Gemini call failed: %s", exc)
            return None

    # ── Event bus handlers ────────────────────────────────────────────────────

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
                    {
                        "task_id": task_id,
                        "intent": safe_intent,
                        "parameters": {"text": user_text, "stt": stt_meta},
                    },
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
                    await bus.emit(
                        "task_failed",
                        {"task_id": task_id, "error": "LLM failed to parse intent."},
                    )
        except Exception as exc:
            self.logger.error("process_intent error for task %s: %s", task_id, exc)
            await bus.emit("task_failed", {"task_id": task_id, "error": str(exc)})

    def _choose_safe_text_intent(self):
        intents = set(self._get_registered_intents())
        for preferred in (
            "generate_text",
            "summarize_text",
            "correct_grammar",
            "translate_text",
        ):
            if preferred in intents:
                return preferred
        return None

    async def process_reasoning(self, event):
        task_id = event.data.get("task_id")
        prompt = event.data.get("prompt")
        result = await self.generate(prompt)
        await bus.emit(
            "reasoning_completed",
            {"task_id": task_id, "response": result},
            source="llm_client",
        )

    # ── Intent parsing helpers ────────────────────────────────────────────────

    def _build_parsing_prompt(self, text: str) -> str:
        allowed_intents = self._get_registered_intents()
        return (
            f'Analyze the user command: "{text}". '
            "Return ONLY strict JSON with this exact schema: "
            '{ "intent": "<one_registered_capability_name>", "parameters": { ... } }. '
            f"Use one of these intents when possible: {json.dumps(allowed_intents)}. "
            "Do not include markdown, <think> blocks, or explanation. Output JSON only."
        )

    async def _ask_intent_with_provider_fallback(self, prompt: str):
        """Try providers in priority order until one succeeds."""
        # 1. OpenRouter (if key + model configured)
        if settings.OPENROUTER_API_KEY and getattr(settings, "OPENROUTER_MODEL", ""):
            result = await self.ask(prompt, provider="openrouter", use_json=True)
            if result and "raw" not in result:
                return result

        # 2. Gemini (if key configured)
        if settings.GEMINI_API_KEY:
            result = await self.ask(prompt, provider="gemini", use_json=True)
            if result and "raw" not in result:
                return result

        # 3. Local Ollama (always available)
        return await self.ask(prompt, provider="local", use_json=True)

    def _get_registered_intents(self) -> list[str]:
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

    async def _repair_intent_with_llm(self, user_text: str, intent_data):
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

    def _fallback_intent_from_user_text(self, user_text: str):
        text = (user_text or "").strip()
        if not text:
            return None
        return {"intent": text, "parameters": {"text": text}}

    async def _semantic_resolve_registered_intent(self, candidate_text: str):
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

    async def _normalize_intent_output(self, task_id: str, user_text: str, intent_data):
        parsed = self._coerce_parsed_intent(intent_data)
        if not parsed:
            repaired = await self._repair_intent_with_llm(user_text, intent_data)
            parsed = self._coerce_parsed_intent(repaired)
            if not parsed:
                resolved = await self._semantic_resolve_registered_intent(user_text)
                if not resolved:
                    return None
                return {
                    "task_id": task_id,
                    "intent": resolved,
                    "parameters": {"text": user_text},
                }

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
                        resolved = await self._semantic_resolve_registered_intent(
                            repaired_intent
                        )
                        if resolved:
                            intent_name = resolved
                        else:
                            return None
                else:
                    return None
            else:
                intent_name = resolved

        return {"task_id": task_id, "intent": intent_name, "parameters": params}


# Global singleton
llm_client = LLMClient()