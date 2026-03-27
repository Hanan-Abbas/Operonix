import asyncio
import json
from core.event_bus import bus
from brain.llm_client import llm_client

class Planner:
    def __init__(self):
        self.name = "planner"
        self.plan_storage = {}

    async def start(self):
        """
        Subscribe to 'capability_mapped' from the CapabilityMapper.
        The flow is: Intent -> Capability -> Planner.
        """
        bus.subscribe("capability_mapped", self.create_plan)
        print("🧠 Planner: Strategist active. Ready to build complex execution paths.")

    async def create_plan(self, event):
        """
        Converts a mapped capability into a concrete list of steps.
        """
        task_id = event.data.get("task_id")
        intent = event.data.get("intent")
        capability = event.data.get("capability")
        args = event.data.get("args", {})
        
        print(f"📝 Planner: Generating strategy for {intent}...")

        # DECISION: Is this a simple template or does it need LLM reasoning?
        if self._needs_llm_reasoning(intent, args):
            steps = await self._generate_llm_steps(intent, args)
        else:
            steps = self._generate_static_steps(intent, args)

        if not steps:
            await bus.emit("task_failed", {
                "task_id": task_id,
                "error": f"Planner failed to generate steps for {intent}"
            }, source="planner")
            return

        # Store for the Execution Tracker
        self.plan_storage[task_id] = steps

        # Emit the plan for the Executor to pick up
        await bus.emit("plan_ready", {
            "task_id": task_id,
            "steps": steps,
            "metadata": {"intent": intent, "capability": capability}
        }, source="planner")

    def _needs_llm_reasoning(self, intent, args):
        """Logic to decide if we need to burn GPU cycles on Ollama."""
        complex_intents = ["write_code", "debug_error", "summarize_and_save", "complex_workflow"]
        # Also trigger LLM if the prompt is very long or vague
        return intent in complex_intents or len(args.get("raw_text", "")) > 100

    async def _generate_llm_steps(self, intent, args):
        """
        Calls the LLM Client to brainstorm the steps.
        """
        prompt = f"""
        Break down this OS task into a sequence of tool calls.
        Task: {intent}
        Details: {args}
        
        Available Tools: file_tool, shell_tool, ui_tool.
        Return ONLY a JSON list of steps. 
        Example: [ {{"tool": "shell_tool", "action": "execute", "args": {{"command": "ls"}}}} ]
        """
        
        response = await llm_client.ask(prompt, use_json=True)
        
        return response.get("steps", []) if isinstance(response, dict) else []

    def _generate_static_steps(self, intent, args):
        """Hardcoded templates for common, simple actions."""
        if intent == "file_create":
            return [
                {"tool": "file_tool", "action": "write", "args": {"path": args.get("name"), "data": args.get("content", "")}}
            ]
        
        if intent == "shell_command":
            return [
                {"tool": "shell_tool", "action": "execute", "args": {"command": args.get("command")}}
            ]
        
        return []

# Global instance
planner = Planner()