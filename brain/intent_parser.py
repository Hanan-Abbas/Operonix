import asyncio
from core.event_bus import bus

class IntentParser:
    def __init__(self):
        # This would eventually be loaded from capabilities/registry.py
        self.supported_intents = [
            "file_create", "file_delete", "file_move", 
            "shell_command", "ui_click", "ui_type", 
            "browser_open", "search_web", "app_launch"
        ]

    async def start(self):
        """Subscribe to the output of the LLM Client."""
        bus.subscribe("intent_parsed", self.validate_and_route)
        print("🧠 Intent Parser: Validator active and monitoring LLM output...")

    async def validate_and_route(self, event):
        """
        Validates if the intent is supported and determines the next step.
        """
        task_id = event.data.get("task_id")
        intent = event.data.get("intent")
        params = event.data.get("parameters")

        # 1. Validation Check
        if intent not in self.supported_intents:
            await bus.emit("task_failed", {
                "task_id": task_id,
                "error": f"Unsupported Intent: '{intent}'. AI attempted an unregistered capability."
            }, source="intent_parser")
            return

        print(f"🎯 Intent Parser: Validated [{intent}] for Task [{task_id}]")

        # 2. Safety & Risk Check (Pre-Planning)
        # Here we decide if we need a confirmation before proceeding
        is_high_risk = self._check_risk(intent, params)

        if is_high_risk:
            await bus.emit("request_user_confirmation", {
                "task_id": task_id,
                "intent": intent,
                "message": f"Are you sure you want to {intent} with parameters {params}?"
            }, source="intent_parser")
        else:
            # 3. Trigger the Planner
            # If safe and valid, we tell the Planner to create the step-by-step instructions
            await bus.emit("intent_validated", {
                "task_id": task_id,
                "intent": intent,
                "parameters": params
            }, source="intent_parser")

    def _check_risk(self, intent, params):
        """Simple risk logic (will be expanded in safety/risk_rules.py)."""
        risky_intents = ["file_delete", "shell_command"]
        if intent in risky_intents:
            return True
        return False

# Global instance
intent_parser = IntentParser()