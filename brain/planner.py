import asyncio
from core.event_bus import bus

class Planner:
    def __init__(self):
        self.plan_storage = {}

    async def start(self):
        """Subscribe to validated intents to begin step-by-step planning."""
        bus.subscribe("intent_validated", self.create_plan)
        print("🧠 Planner: Strategist active. Ready to break down tasks.")

    async def create_plan(self, event):
        """
        Converts a high-level intent into a list of executable steps.
        """
        task_id = event.data.get("task_id")
        intent = event.data.get("intent")
        params = event.data.get("parameters")

        print(f"📝 Planner: Generating sequence for {intent}...")

        # 1. Logic for step generation
        # In a full version, this might call the LLM again to 'think' through steps
        steps = self._generate_steps(intent, params)

        if not steps:
            await bus.emit("task_failed", {
                "task_id": task_id,
                "error": f"Planner could not generate steps for {intent}"
            }, source="planner")
            return

        # 2. Store the plan
        self.plan_storage[task_id] = {
            "current_step": 0,
            "total_steps": len(steps),
            "steps": steps
        }

        # 3. Emit the plan so the Executor can take over
        await bus.emit("plan_ready", {
            "task_id": task_id,
            "steps": steps,
            "metadata": {"intent": intent, "params": params}
        }, source="planner")

    def _generate_steps(self, intent, params):
        """
        Hardcoded logic for basic intents; complex ones will use LLM.
        Each step defines: tool, action, and arguments.
        """
        if intent == "file_create":
            filename = params.get("name", "untitled.txt")
            content = params.get("content", "")
            return [
                {"tool": "file_tool", "action": "check_exists", "args": {"path": filename}},
                {"tool": "file_tool", "action": "write", "args": {"path": filename, "data": content}},
                {"tool": "ui_tool", "action": "notify", "args": {"message": f"Created {filename}"}}
            ]

        if intent == "shell_command":
            return [
                {"tool": "shell_tool", "action": "execute", "args": {"command": params.get("command")}}
            ]

        # Fallback for unknown sequences
        return []

# Global instance
planner = Planner()