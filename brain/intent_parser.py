import asyncio
import logging
import json
from pathlib import Path
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

    def __init__(self):
        self.logger = logging.getLogger("IntentParser")

    async def start(self):
        """Subscribe to the output of the LLM Client."""
        bus.subscribe("intent_parsed", self.validate_and_route)
        self.logger.info("🛡️ Intent Parser active: Monitoring LLM output...")

        # Dynamically register supported intents from the capability registry
        try:
            from capabilities.registry import capability_registry
            supported = capability_registry.get_all_intents()
        except ImportError:
            self.logger.warning("Capability registry not found. Waiting for dynamic registration.")
            supported = []

        if supported:
            await vector_store.add_intents(supported)

    async def validate_and_route(self, event):
        """Validates if the intent is supported and determines the next step."""
        task_id = event.data.get("task_id")
        raw_intent = event.data.get("intent")
        params = event.data.get("parameters", {})

        self.logger.info(f"🔍 Validating intent: '{raw_intent}' for task {task_id}")

        # 1. Check for exact matches or high-confidence vector matches
        match = await vector_store.search_intent(raw_intent)
        
        if not match:
            self.logger.error(f"❌ Unknown intent: {raw_intent}. Aborting task.")
            await bus.emit("task_failed", {"task_id": task_id, "error": f"Unsupported intent: {raw_intent}"})
            return

        resolved_intent = match['intent']
        
        # 2. Risk Assessment (Crucial for source code changes!)
        requires_confirmation = await self._is_risky(resolved_intent, params)

        if requires_confirmation:
            self.logger.warning(f"⚠️ High-risk operation detected: {resolved_intent}. Escalating to Confirmation Manager.")
            await bus.emit("request_confirmation", {
                "task_id": task_id,
                "intent": resolved_intent,
                "parameters": params,
                "risk_level": "high"
            })
        else:
            # 3. Route to Orchestrator for final mapping
            await bus.emit("intent_validated", {
                "task_id": task_id,
                "intent": resolved_intent,
                "parameters": params
            })

    async def _is_risky(self, intent: str, params: dict) -> bool:
        """
        🛡️ Evaluates if an action could damage the system or compromise security.
        """
        # Static check against dynamic settings
        if intent in getattr(settings, "RISKY_INTENTS", []):
            return True

        # Specific path protection for code modification
        if intent == "modify_code":
            target_file = params.get("file_path", "")
            restricted_dirs = ["core/", "safety/", "brain/"]
            if any(target_file.startswith(rd) for rd in restricted_dirs):
                self.logger.warning(f"🛡️ Core modification attempt blocked: {target_file}")
                return True

        # Dynamic LLM-based risk evaluation
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
        except Exception as e:
            self.logger.error(f"Risk evaluation failed: {e}. Defaulting to SAFE/Locked mode.")
            return True

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