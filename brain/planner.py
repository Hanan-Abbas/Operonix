import asyncio
from core.event_bus import bus

class Planner:
    def __init__(self):
        self.plan_storage = {}

    async def start(self):
        """Subscribe to validated intents to begin step-by-step planning."""
        bus.subscribe("intent_validated", self.create_plan)
        print("🧠 Planner: Strategist active. Ready to break down tasks.")

    