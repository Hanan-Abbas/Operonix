import json
import logging
from brain.llm_client import llm_client
from core.event_bus import bus

class Planner:

    def __init__(self):
        self.logger = logging.getLogger("Planner")
        self.plan_storage = {}

    async def start(self):
        bus.subscribe("request_planning", self.create_plan)
        self.logger.info("Planner: Strategist active. Ready to build execution paths.")

    async def create_plan(self, event):
        task_id = event.data.get("task_id")
        intent = event.data.get("intent")
        args = event.data.get("parameters", {})
        suggested_tool = event.data.get("suggested_tool")

        self.logger.info(f"📝 Planner: Generating strategy for {intent}...")

        # 🟢 DYNAMIC CHECK: Instead of hardcoded lists, we check the length of the raw text 
        # or evaluate if the operation is multi-step.
        if self._needs_llm_reasoning(intent, args):
            steps = await self._generate_llm_steps(intent, args, suggested_tool)
        else:
            steps = self._generate_static_steps(intent, args, suggested_tool)

        if not steps:
            bus.publish(
                "task_failed",
                data={
                    "task_id": task_id,
                    "error": f"Planner failed to generate steps for {intent}",
                },
                source="planner",
            )
            return

        self.plan_storage[task_id] = steps

        bus.publish(
            "task_dispatched",
            data={
                "task_id": task_id,
                "intent": intent,
                "steps": steps,
                "context": event.data.get("context", {}),
            },
            source="planner",
        )

        self.logger.info(f"🚀 Planner: Dispatched task [{task_id}] to Safety Validator.")

    def _needs_llm_reasoning(self, intent, args) -> bool:
        """
        🟢 ZERO HARDCODING: We dictate reasoning needs purely based on complexity.
        If there are lots of parameters, or big text, it needs a planner.
        """
        raw = args.get("raw_text") or args.get("content") or ""
        
        # If the input text is massive, it implies a complex generation task
        if isinstance(raw, str) and len(raw) > 300:
            return True
            
        # If we have more than 3 distinct parameters, it's likely a complex orchestration
        if isinstance(args, dict) and len(args) > 3:
            return True
            
        return False

    async def _generate_llm_steps(self, intent, args, suggested_tool):
        """🟢 DYNAMIC INJECTION: We fetch all available capabilities from the registry!"""
        
        try:
            from capabilities.registry import capability_registry
            available_capabilities = capability_registry.get_all_intents()
        except ImportError:
            available_capabilities = ["read_file", "write_file", "run_command", "type_text"]

        prompt = f"""
        Break down this OS task into executable steps for an automation agent.
        Task intent: {intent}
        Suggested approach/tool: {suggested_tool}
        Parameters: {json.dumps(args)}

        Return JSON strictly matching this structure: 
        {{ "steps": [ {{ "action": "<capability_name>", "args": {{ ... }} }} ] }}
        
        You are allowed to use ONLY these capability names: {available_capabilities}
        """

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
        step = {"action": intent, "args": dict(args or {})}
        if suggested_tool:
            step["suggested_tool"] = suggested_tool
            
        return [step]

# Global instance
planner = Planner()