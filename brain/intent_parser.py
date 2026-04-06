import asyncio
import logging
import json
from core.config import settings
from core.event_bus import bus
from memory.vector_store import vector_store
# 🟢 NEW: Assuming you are using an LLM client to talk to Ollama
from brain.llm_client import llm_client 


class IntentParser:

    def __init__(self):
        self.logger = logging.getLogger("IntentParser")

    async def start(self):
        """Subscribe to the output of the LLM Client."""
        bus.subscribe("intent_parsed", self.validate_and_route)
        self.logger.info("Intent Parser active and monitoring LLM output...")

        # 🟢 DYNAMIC: We query the registry directly. No hardcoded fallback lists!
        try:
            from capabilities.registry import capability_registry
            supported = capability_registry.get_all_intents()
        except ImportError:
            self.logger.warning("Capability registry not found. Waiting for dynamic registration.")
            supported = []

        # We teach the vector DB what our current capabilities are dynamically!
        if supported:
            await vector_store.add_intents(supported)

    async def validate_and_route(self, event):
        """Validates if the intent is supported and determines the next step."""
        task_id = event.data.get("task_id")
        raw_intent = event.data.get("intent")
        params = event.data.get("parameters", {})

        self.logger.info(f"🔍 Searching VectorDB for closest match to: '{raw_intent}'")
        
        # Search the database for semantic matching
        matched_intent, confidence = await vector_store.search_closest_intent(raw_intent)

        if matched_intent and confidence > 0.75:
            self.logger.info(
                f"🎯 Vector Match: '{raw_intent}' -> '{matched_intent}' (Conf: {confidence:.2f})"
            )
            intent = matched_intent
        else:
            intent = raw_intent

        # Validation Check against whatever is currently in the registry
        try:
            from capabilities.registry import capability_registry
            supported = capability_registry.get_all_intents()
        except ImportError:
            supported = []

        if intent not in supported:
            bus.publish(
                "task_failed",
                data={
                    "task_id": task_id,
                    "error": f"Unsupported Intent: '{intent}'. Unregistered capability.",
                },
                source="intent_parser",
            )
            return

        print(f"🎯 Intent Parser: Validated [{intent}] for Task [{task_id}]")

        # 🟢 DYNAMIC RISK CHECK: No hardcoded lists. We let Ollama grade the safety!
        is_high_risk = await self._check_risk_dynamically(intent, params)

        if is_high_risk and settings.SAFE_MODE:
            bus.publish(
                "request_user_confirmation",
                data={
                    "task_id": task_id,
                    "intent": intent,
                    "message": f"I evaluated this request as high risk. Are you sure you want to {intent}?",
                },
                source="intent_parser",
            )
        else:
            # Trigger the Planner
            bus.publish(
                "intent_validated",
                data={
                    "task_id": task_id,
                    "intent": intent,
                    "parameters": params,
                },
                source="intent_parser",
            )

    async def _check_risk_dynamically(self, intent, params):
        """
        🟢 ZERO HARDCODING: Uses Ollama to evaluate if an intent + parameters 
        is dangerous based on context, rather than a rigid list.
        """
        # First, preserve your hardcoded system path checks as a baseline guardrail
        target_path = params.get("path") or params.get("target") or params.get("file_path")
        if target_path:
            for restricted in settings.RESTRICTED_PATHS:
                if str(target_path).startswith(restricted):
                    self.logger.warning(f"Blocked attempt to modify restricted path: {target_path}")
                    return True

        # Now, we ask Ollama to judge the risk factor!
        prompt = f"""
        Rate the destructiveness or security risk of this operation on a scale of 1 to 10.
        Operation: {intent}
        Parameters: {json.dumps(params)}
        
        Consider file deletions, terminal executions, or web requests as high risk (7-10).
        Consider simple reading, scrolling, or creating new files as low risk (1-5).
        
        Return ONLY a JSON object with a single key 'risk_score' mapping to an integer.
        Example: {{"risk_score": 8}}
        """

        try:
            # We assume your llm_client handles talking to Ollama
            response = await llm_client.generate(prompt, format="json")
            data = json.loads(response)
            risk_score = data.get("risk_score", 1)
            
            self.logger.info(f"🛡️ Dynamic Risk Evaluator gave score: {risk_score}/10")
            
            # If the risk score is 7 or higher, we demand confirmation!
            return risk_score >= 7

        except Exception as e:
            self.logger.error(f"Failed to dynamically evaluate risk: {e}. Falling back to safe mode.")
            # If AI fails to respond, assume high risk to be safe!
            return True


# Global instance
intent_parser = IntentParser()