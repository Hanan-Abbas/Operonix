import asyncio
import logging
from core.config import settings  # <-- IMPORT CONFIG
from core.event_bus import bus


class IntentParser:

    def __init__(self):
        self.logger = logging.getLogger("IntentParser")

        # Fallback intents if registry.py isn't loaded yet
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
        # Listen for raw LLM outputs needing parsing and validation
        bus.subscribe("intent_parsed", self.validate_and_route)
        self.logger.info("Intent Parser active and monitoring LLM output...")

    async def validate_and_route(self, event):
        """Validates if the intent is supported and determines the next step."""
        task_id = event.data.get("task_id")
        intent = event.data.get("intent")
        params = event.data.get("parameters", {})

        # 1. Validation Check against the registry (or fallback)
        supported = self.fallback_intents
        try:
            from capabilities.registry import capability_registry

            supported = capability_registry.get_all_intents() or supported
        except ImportError:
            self.logger.warning(
                "Could not load capability_registry. Using fallback intents."
            )

        if intent not in supported:
            # Using publish instead of emit for safe execution on the queue
            bus.publish(
                "task_failed",
                data={
                    "task_id": task_id,
                    "error": f"Unsupported Intent: '{intent}'. AI attempted an unregistered capability.",
                },
                source="intent_parser",
            )
            return

        print(
            f"🎯 Intent Parser: Validated [{intent}] for Task [{task_id}]"
        )

        # 2. Safety & Risk Check (Pre-Planning)
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

        # Check 1: Is the intent inherently dangerous?
        if intent in risky_intents:
            return True

        # Check 2: Is the AI trying to modify a restricted system path?
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