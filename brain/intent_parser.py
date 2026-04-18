"""
brain/intent_parser.py

Validation layer: ensures LLM-interpreted intents exist in the capability
registry and evaluates risk before allowing execution.

Panel integration
─────────────────
The suggestion engine calls `intent_parser.parse(text)` synchronously
(wrapped in asyncio.to_thread) to power live suggestions as the user types.
`parse()` is a thin synchronous wrapper that runs the async `_parse_async`
pipeline in a safe way — it never blocks the Qt thread.
"""
from __future__ import annotations

import asyncio
import json
import logging
from typing import Any

from core.config import settings
from core.event_bus import bus
from memory.vector_store import vector_store
from brain.llm_client import llm_client


class IntentParser:
    """
    🔍 The Validation Layer.
    Ensures that the LLM's interpreted intent exists in our capability registry
    and evaluates the risk level before allowing execution.
    """

    def __init__(self) -> None:
        self.logger = logging.getLogger("IntentParser")

    async def start(self) -> None:
        """Subscribe to the output of the LLM Client."""
        bus.subscribe("intent_parsed", self.validate_and_route)
        self.logger.info("🛡️ Intent Parser active: Monitoring LLM output...")

        try:
            from capabilities.registry import capability_registry
            supported = capability_registry.get_all_names()
        except Exception:
            self.logger.warning("Capability registry not found. Waiting for dynamic registration.")
            supported = []

        if supported:
            await vector_store.add_intents(supported)

    # ── Panel-facing synchronous interface ────────────────────────────────────

    def parse(self, text: str) -> dict[str, Any]:
        """
        Synchronous entry point called by the panel's suggestion engine.
        Runs the async pipeline in a new event loop if no loop is running,
        or schedules it safely if one is already running.

        Returns a dict with at minimum:
            {
                "intent":     str | None,
                "confidence": float,          # 0.0 – 1.0
                "parameters": dict,
            }
        """
        try:
            # If there is already a running loop (asyncio or Qt async bridge)
            # we cannot call run_until_complete — use a new thread-level loop.
            try:
                loop = asyncio.get_running_loop()
            except RuntimeError:
                loop = None

            if loop and loop.is_running():
                # We're inside an async context — run in a fresh OS thread loop.
                import concurrent.futures
                with concurrent.futures.ThreadPoolExecutor(max_workers=1) as pool:
                    future = pool.submit(self._run_parse_sync, text)
                    return future.result(timeout=5.0)
            else:
                return asyncio.run(self._parse_async(text))

        except Exception as exc:  # noqa: BLE001
            self.logger.warning("IntentParser.parse: failed for %r — %s", text, exc)
            return {"intent": None, "confidence": 0.0, "parameters": {}}

    def _run_parse_sync(self, text: str) -> dict[str, Any]:
        """Run _parse_async in a brand-new event loop (used from non-async threads)."""
        return asyncio.run(self._parse_async(text))

    async def _parse_async(self, text: str) -> dict[str, Any]:
        """
        Async core of parse(): asks the LLM to classify the intent,
        then resolves and validates it against the registry.
        """
        if not text.strip():
            return {"intent": None, "confidence": 0.0, "parameters": {}}

        # Ask LLM to extract intent + parameters from natural language.
        prompt = f"""
Extract the intent and parameters from this user command.
Return ONLY JSON with keys: intent (snake_case string), confidence (0.0-1.0 float), parameters (dict).

User command: {text}

Example output: {{"intent": "open_file", "confidence": 0.92, "parameters": {{"path": "main.py"}}}}
"""
        try:
            raw = await asyncio.wait_for(
                llm_client.generate(prompt, use_json=True),
                timeout=4.0,
            )
            if isinstance(raw, dict):
                llm_intent = raw.get("intent", "")
                confidence = float(raw.get("confidence", 0.5))
                parameters = raw.get("parameters", {})
            else:
                llm_intent = str(raw)
                confidence = 0.5
                parameters = {}
        except Exception as exc:  # noqa: BLE001
            self.logger.debug("_parse_async: LLM failed — %s", exc)
            # Graceful degradation: attempt a keyword-based guess.
            llm_intent = self._keyword_guess(text)
            confidence = 0.3 if llm_intent else 0.0
            parameters = {}

        # Resolve against the capability registry (exact + semantic).
        resolved = await self._resolve_intent(llm_intent)
        if resolved:
            return {"intent": resolved, "confidence": confidence, "parameters": parameters}

        # If resolution failed, still return the raw LLM guess so the
        # suggestion engine can show a low-confidence ui-fallback row.
        return {"intent": llm_intent or None, "confidence": confidence * 0.4, "parameters": parameters}

    @staticmethod
    def _keyword_guess(text: str) -> str:
        """
        Bare-minimum keyword matcher used only when the LLM is unavailable.
        Maps common English verbs to generic intent names.  Never hardcodes
        application-specific logic — just structural command words.
        """
        lower = text.lower()
        mapping = {
            ("open", "launch", "start", "run"):    "open_file",
            ("close", "quit", "exit", "kill"):     "close_app",
            ("search", "find", "look"):            "web_search",
            ("write", "create", "make", "new"):    "create_file",
            ("delete", "remove", "trash"):         "file_delete",
            ("read", "show", "display", "print"):  "read_file",
            ("edit", "modify", "change", "update"): "modify_code",
            ("copy", "duplicate"):                 "copy_file",
            ("move", "rename"):                    "move_file",
        }
        for verbs, intent in mapping.items():
            if any(v in lower for v in verbs):
                return intent
        return ""

    # ── EventBus path (unchanged from original) ───────────────────────────────

    async def validate_and_route(self, event: Any) -> None:
        """Validates if the intent is supported and determines the next step."""
        task_id = event.data.get("task_id")
        raw_intent = event.data.get("intent")
        params = event.data.get("parameters", {})

        self.logger.info("🔍 Validating intent: '%s' for task %s", raw_intent, task_id)

        resolved_intent = await self._resolve_intent(raw_intent)
        if not resolved_intent:
            self.logger.error("❌ Unknown intent: %s. Aborting task.", raw_intent)
            await bus.emit("task_failed", {"task_id": task_id, "error": f"Unsupported intent: {raw_intent}"})
            return

        requires_confirmation = await self._is_risky(resolved_intent, params)

        if requires_confirmation:
            self.logger.warning("⚠️ High-risk operation detected: %s. Escalating.", resolved_intent)
            await bus.emit("request_confirmation", {
                "task_id": task_id,
                "intent": resolved_intent,
                "parameters": params,
                "risk_level": "high",
            })
        else:
            await bus.emit("intent_validated", {
                "task_id": task_id,
                "intent": resolved_intent,
                "parameters": params,
            })

    async def _resolve_intent(self, raw_intent: str) -> str | None:
        """
        1) Exact capability registry hit
        2) Semantic nearest intent from vector store
        """
        if not raw_intent:
            return None

        try:
            from capabilities.registry import capability_registry
            if capability_registry.get(raw_intent) is not None:
                return raw_intent
        except Exception:
            pass

        try:
            closest_intent, confidence = await vector_store.search_closest_intent(raw_intent)
            threshold = float(getattr(settings, "INTENT_MATCH_MIN_CONFIDENCE", 0.35))
            if closest_intent and confidence >= threshold:
                self.logger.info(
                    "🔎 Resolved '%s' → '%s' (confidence=%.2f)",
                    raw_intent, closest_intent, confidence,
                )
                return closest_intent
        except Exception as exc:  # noqa: BLE001
            self.logger.debug("_resolve_intent: vector search failed — %s", exc)

        return None

    async def _is_risky(self, intent: str, params: dict) -> bool:
        """Evaluates if an action could damage the system or compromise security."""
        if intent in getattr(settings, "RISKY_INTENTS", []):
            return True

        if intent == "modify_code":
            target_file = params.get("file_path", "")
            restricted_dirs = getattr(settings, "RESTRICTED_PATHS", ["core/", "safety/", "brain/"])
            if any(str(target_file).startswith(str(rd)) for rd in restricted_dirs):
                self.logger.warning("🛡️ Core modification attempt blocked: %s", target_file)
                return True

        prompt = f"""
Rate the security risk of this AI operation (1-10):
Operation: {intent}
Parameters: {json.dumps(params)}

Rule: File deletions, code modifications, or shell commands are 8-10.
Rule: Reading files or checking system status are 1-3.

Return ONLY JSON: {{"risk_score": <int>}}
"""
        try:
            response = await llm_client.generate(prompt, use_json=True)
            risk_score = response.get("risk_score", 1)
            return risk_score >= 7
        except Exception as exc:  # noqa: BLE001
            self.logger.error("Risk evaluation failed: %s. Defaulting to SAFE/Locked mode.", exc)
            return True


# Global instance
intent_parser = IntentParser()