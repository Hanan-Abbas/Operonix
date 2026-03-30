import json
import logging
from core.config import settings  # <-- IMPORT CONFIG
from core.event_bus import bus
from brain.llm_client import llm_client


class Planner:
    def __init__(self):
        self.logger = logging.getLogger("Planner")
        self.name = "planner"
        self.plan_storage = {}

    async def start(self):
        # Updated to subscribe to the 'intent_validated' event coming from IntentParser
        bus.subscribe("capability_mapped", self.create_plan)  
        self.logger.info("Planner: Strategist active. Ready to build execution paths.")

    async def create_plan(self, event):
        task_id = event.data.get("task_id")
        intent = event.data.get("intent")
        # Unified to extract from 'parameters' which IntentParser passes down
        args = event.data.get("parameters", {})

        self.logger.info(f"📝 Planner: Generating strategy for {intent}...")

        if self._needs_llm_reasoning(intent, args):
            steps = await self._generate_llm_steps(intent, args)
        else:
            steps = self._generate_static_steps(intent, args)

        if not steps:
            bus.publish(
                "task_failed",
                {"task_id": task_id, "error": f"Planner failed to generate steps for {intent}"},
                source="planner",
            )
            return

        self.plan_storage[task_id] = steps

        # Successfully created steps -> Fire to the executor!
        bus.publish(
            "plan_ready",
            {"task_id": task_id, "steps": steps, "context": {}, "metadata": {"intent": intent}},
            source="planner",
        )

    def _needs_llm_reasoning(self, intent, args):
        complex_intents = {
            "write_code",
            "debug_error",
            "summarize_and_save",
            "complex_workflow",
            "code_generate",
            "code_analyze",
            "generate_text",
        }
        if intent in complex_intents:
            return True
        raw = args.get("raw_text") or ""
        return isinstance(raw, str) and len(raw) > 400

    async def _generate_llm_steps(self, intent, args):
        prompt = f"""
        Break down this OS task into executable steps for an automation agent.
        Task intent: {intent}
        Parameters: {json.dumps(args)}

        Return JSON: {{ "steps": [ {{ "action": "<capability_name>", "args": {{ ... }} }} ] }}
        Use capability names like write_file, read_file, run_command, type_text, click, open_url, search_web.
        """
        
        # We pass settings.FAST_LLM here. Breaking down steps is a fast, structured job!
        # This keeps your project fast and reduces token costs.
        response = await llm_client.ask(prompt, use_json=True, model=settings.FAST_LLM)
        
        if not isinstance(response, dict):
            return []
            
        steps = response.get("steps", [])
        out = []
        for s in steps:
            if isinstance(s, dict) and s.get("action"):
                out.append({"action": s["action"], "args": s.get("args", {})})
        return out

    def _generate_static_steps(self, intent, args):
        """One registry-backed step; executor validates via capability_registry then dispatches to tools."""
        return [{"action": intent, "args": dict(args or {})}]


# Global instance
planner = Planner()