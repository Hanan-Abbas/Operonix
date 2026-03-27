import asyncio
from core.event_bus import bus
# from brain.llm_client import llm  # You will build this next

class Planner:
    def __init__(self):
        self.plan_storage = {}

    async def start(self):
        bus.subscribe("capability_mapped", self.create_plan)
        print("🧠 Planner: Ready to strategize complex tasks.")

    async def create_plan(self, event):
        task_id = event.data.get("task_id")
        intent = event.data.get("intent")
        capability = event.data.get("capability")
        args = event.data.get("args")

        print(f"📝 Planner: Strategizing for {intent} using {capability}...")

        # ✅ DYNAMIC STEP GENERATION
        # If it's a complex task (like coding), we ask the LLM to generate steps
        if self._is_complex_task(intent):
            steps = await self._ask_llm_for_steps(intent, capability, args)
        else:
            steps = self._generate_static_steps(intent, args)

        if not steps:
            await bus.emit("task_failed", {"task_id": task_id, "error": "No steps generated."})
            return

        # Emit the plan to the Executor
        await bus.emit("plan_ready", {
            "task_id": task_id,
            "steps": steps,
            "context": {"active_app": args.get("active_app")}
        }, source="planner")

    def _is_complex_task(self, intent):
        # Intents that require "Thinking" rather than "Templates"
        complex_intents = ["write_code", "debug_error", "refactor_project", "research_topic"]
        return intent in complex_intents

    async def _ask_llm_for_steps(self, intent, capability, args):
        """
        This is where the 'Coding' magic happens. 
        It asks the LLM: 'Give me a JSON list of steps to achieve [intent]'.
        """
        # prompt = f"Break down the following task into steps for an OS Agent: {intent} with {args}"
        # response = await llm.generate_json(prompt)
        # return response['steps']
        return [{"tool": "shell_tool", "action": "execute", "args": {"command": "echo Thinking..."}}]

    def _generate_static_steps(self, intent, args):
        """Your existing hardcoded logic for simple stuff like 'shut down' or 'create file'."""
        if intent == "file_create":
            return [
                {"tool": "file_tool", "action": "write", "args": {"path": args.get("path"), "data": args.get("data")}}
            ]
        return []

planner = Planner()