import json
import logging
from brain.llm_client import llm_client
from core.event_bus import bus


class Planner:

    def __init__(self):
        self.logger = logging.getLogger("Planner")
        self.plan_storage = {}

    async def start(self):
        # 🔗 FIX: Decision Engine now publishes 'request_planning'
        bus.subscribe("request_planning", self.create_plan)
        self.logger.info(
            "Planner: Strategist active. Ready to build execution paths."
        )

    async def create_plan(self, event):
        task_id = event.data.get("task_id")
        intent = event.data.get("intent")
        args = event.data.get("parameters", {})
        suggested_tool = event.data.get("suggested_tool")

        self.logger.info(f"📝 Planner: Generating strategy for {intent}...")

        # 🔄 UPGRADE: Pass the suggested tool context into the steps
        if self._needs_llm_reasoning(intent, args):
            steps = await self._generate_llm_steps(intent, args, suggested_tool)
        else:
            steps = self._generate_static_steps(intent, args, suggested_tool)

        if not steps:
            bus.publish(
                "task_failed",
                {
                    "task_id": task_id,
                    "error": f"Planner failed to generate steps for {intent}",
                },
                source="planner",
            )
            return

        self.plan_storage[task_id] = steps

        bus.publish(
            "task_dispatched",
            {
                "task_id": task_id,
                "intent": intent,
                "steps": steps,
                "context": event.data.get("context", {}),
            },
            source="planner",
        )

        self.logger.info(
            f"🚀 Planner: Dispatched task [{task_id}] to Safety Validator."
        )

    def _needs_llm_reasoning(self, intent, args) -> bool:
        """Dynamically decides if an LLM breakdown is needed without hardcoding."""
        intent_lower = intent.lower() if intent else ""
        
        # 🔄 UPGRADE: Prefix matching instead of a hardcoded exact list
        llm_heavy_prefixes = ["write_", "debug_", "complex_", "generate_"]
        
        if any(intent_lower.startswith(prefix) for prefix in llm_heavy_prefixes):
            return True
            
        raw = args.get("raw_text") or ""
        return isinstance(raw, str) and len(raw) > 400

    async def _generate_llm_steps(self, intent, args, suggested_tool):
        prompt = f"""
        Break down this OS task into executable steps for an automation agent.
        Task intent: {intent}
        Suggested approach/tool: {suggested_tool}
        Parameters: {json.dumps(args)}

        Return JSON: {{ "steps": [ {{ "action": "<capability_name>", "args": {{ ... }} }} ] }}
        Use capability names like write_file, read_file, run_command, type_text, click, open_url, search_web.
        """

        # 🔗 FIX: Your llm_client uses 'provider' (like 'deepseek' or 'gemini'), not 'model'.
        # For fast structured generation, deepseek or local is usually perfect.
        response = await llm_client.ask(
            prompt, provider="deepseek", use_json=True
        )

        if not isinstance(response, dict):
            return []

        steps = response.get("steps", [])
        out = []
        for s in steps:
            if isinstance(s, dict) and s.get("action"):
                out.append({"action": s["action"], "args": s.get("args", {})})
        return out

    def _generate_static_steps(self, intent, args, suggested_tool):
        """One registry-backed step; executor validates via
        capability_registry then dispatches to tools.
        """
        step = {"action": intent, "args": dict(args or {})}
        if suggested_tool:
            step["suggested_tool"] = suggested_tool
            
        return [step]


# Global instance
planner = Planner()