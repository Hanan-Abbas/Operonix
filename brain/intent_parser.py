import asyncio
import logging
from core.config import settings
from core.event_bus import bus
# 🔄 NEW: Import your vector store!
from memory.vector_store import vector_store


class IntentParser:

    def __init__(self):
        self.logger = logging.getLogger("IntentParser")
        self.fallback_intents = [
            "file_create",
            "file_delete",
            "file_move",
            "shell_command",
            "ui_click",
            "ui_type",
            "browser_open",
            "search_web",
            "app_launch",
        ]

    async def start(self):
        """Subscribe to the output of the LLM Client."""
        bus.subscribe("intent_parsed", self.validate_and_route)
        self.logger.info("Intent Parser active and monitoring LLM output...")

        # 🔄 Populate the vector store with official intents on startup
        try:
            from capabilities.registry import capability_registry

            supported = capability_registry.get_all_intents() or self.fallback_intents
        except ImportError:
            supported = self.fallback_intents

        # We teach the vector DB what our official capabilities are!
        await vector_store.add_intents(supported)

    async def validate_and_route(self, event):
        """Validates if the intent is supported and determines the next step."""
        task_id = event.data.get("task_id")
        raw_intent = event.data.get("intent")
        params = event.data.get("parameters", {})

        # -----------------------------------------------------------------
        # 🔄 NEW: Universal Vector Search (No hardcoding!)
        # -----------------------------------------------------------------
        self.logger.info(f"🔍 Searching VectorDB for closest match to: '{raw_intent}'")
        
        # Search the database. It returns the best match and a confidence score.
        matched_intent, confidence = await vector_store.search_closest_intent(raw_intent)

        # If the DB is highly confident it found a match, we rewrite it!
        if matched_intent and confidence > 0.75:
            self.logger.info(
                f"🎯 Vector Match: '{raw_intent}' -> '{matched_intent}' (Conf: {confidence:.2f})"
            )
            intent = matched_intent
        else:
            # If no good match, we keep the raw intent and let validation handle it
            intent = raw_intent

        # 1. Validation Check against the registry
        try:
            from capabilities.registry import capability_registry
            supported = capability_registry.get_all_intents() or self.fallback_intents
        except ImportError:
            supported = self.fallback_intents

        if intent not in supported:
            bus.publish(
                "task_failed",
                data={
                    "task_id": task_id,
                    "error": f"Unsupported Intent: '{intent}'. AI attempted an unregistered capability.",
                },
                source="intent_parser",
            )
            return

        print(f"🎯 Intent Parser: Validated [{intent}] for Task [{task_id}]")

        # 2. Safety & Risk Check
        is_high_risk = self._check_risk(intent, params)

        if is_high_risk and settings.SAFE_MODE:
            bus.publish(
                "request_user_confirmation",
                data={
                    "task_id": task_id,
                    "intent": intent,
                    "message": f"Are you sure you want to {intent} with parameters {params}?",
                },
                source="intent_parser",
            )
        else:
            # 3. Trigger the Planner
            bus.publish(
                "intent_validated",
                data={
                    "task_id": task_id,
                    "intent": intent,
                    "parameters": params,
                },
                source="intent_parser",
            )

    def _check_risk(self, intent, params):
        """Advanced risk logic utilizing core/config.py."""
        risky_intents = ["file_delete", "shell_command"]

        if intent in risky_intents:
            return True

        target_path = params.get("path") or params.get("target")
        if target_path:
            for restricted in settings.RESTRICTED_PATHS:
                if str(target_path).startswith(restricted):
                    self.logger.warning(
                        f"Blocked attempt to modify restricted path: {target_path}"
                    )
                    return True

        return False


# Global instance
intent_parser = IntentParser()