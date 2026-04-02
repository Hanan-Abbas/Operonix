import asyncio
import logging
from typing import Any, Dict
from core.config import settings
from core.event_bus import bus
from capabilities.registry import capability_registry


class DecisionEngine:
    """The traffic cop of the AI.

    Prioritizes tasks, resolves execution pathways, and determines the best tool 
    via the dynamic pipeline (Plugin -> API -> CLI -> UI) without hardcoding.
    """

    def __init__(self):
        self.logger = logging.getLogger("DecisionEngine")
        self.task_queue = asyncio.PriorityQueue()
        self.active_tasks = {}

        # 🔄 UPGRADE: Fallback scoring instead of hardcoded strings!
        # We classify by intent prefix to get a baseline priority score.
        self.PREFIX_PRIORITIES = {
            "emergency": 100,
            "security": 90,
            "stop": 90,
            "cancel": 85,
            "voice_": 50,
            "ui_": 30,
            "click": 30,
            "type_": 30,
            "file_": 20,
            "read_": 20,
            "write_": 20,
            "search_": 10,
            "web_": 10,
        }

    async def start(self):
        """Subscribe to MAPPED intents and start processing loop."""
        # 🔗 FIX: We listen AFTER the Vector DB handles mapping!
        bus.subscribe("capability_mapped", self.enqueue_task)

        # Start the background worker that feeds the pipeline
        asyncio.create_task(self._process_queue())
        self.logger.info(
            "🧠 Decision Engine: Online. Listening to Vector Mapper."
        )

    async def enqueue_task(self, event):
        """Receives a mapped intent and places it in the priority queue."""
        task_data = event.data
        intent = task_data.get("intent")
        task_id = task_data.get("task_id")

        # Determine priority dynamically
        priority_score = self._calculate_priority(intent, task_data)

        # PriorityQueue in python sorts lowest-first, so we invert the score
        await self.task_queue.put((-priority_score, task_data))

        self.logger.info(
            f"📥 Task [{task_id}] ({intent}) queued with priority score: {priority_score}"
        )

    def _calculate_priority(self, intent: str, task_data: Dict[str, Any]) -> int:
        """Calculates a numeric priority score dynamically based on prefix matching."""
        intent = intent.lower() if intent else ""
        
        # 🔄 UPGRADE: Dynamic Prefix Matching (No rigid hardcoding)
        score = 15  # Baseline fallback score
        for prefix, weight in self.PREFIX_PRIORITIES.items():
            if intent.startswith(prefix):
                score = weight
                break

        # Boost if it's directly from the user's active session
        if task_data.get("source") == "user_foreground":
            score += 25

        return score

    async def _resolve_execution_tool(self, intent: str, context: dict) -> str:
        """Determines the best tool following the Execution Priority Pipeline:
        1. Plugin (App-Specific)
        2. Native API / File
        3. CLI / Shell
        4. UI Automation (Last Resort)
        """
        # (Assuming you have access to your tool/plugin registries in practice)
        
        # 🥇 1. Check for Active App Plugin
        active_app = context.get("active_window", "")
        # Dummy check: In reality, you'd ask plugin_registry.get_for_app(active_app)
        if active_app and intent.startswith("app_"): 
            return "plugin_runner"

        # 🥈 2. File / Native API operations
        if any(x in intent for x in ["file_", "read_", "write_", "delete_"]):
            return "file_tool"

        # 🥉 3. Shell / CLI commands
        if any(x in intent for x in ["run_", "execute_", "git_", "install_"]):
            return "shell_tool"

        # 🔴 4. UI Automation Fallback
        if any(x in intent for x in ["click", "type_", "scroll", "move_"]):
            return "ui_tool"

        return "api_tool" # Catch-all background API fallback

    async def _process_queue(self):
        """Continuously pulls the highest priority task and hands it to the planner."""
        while True:
            try:
                # Wait for next priority item
                priority, task_data = await self.task_queue.get()
                task_id = task_data.get("task_id")
                intent = task_data.get("intent")
                context = task_data.get("context", {})

                self.logger.info(
                    f"🧠 Decision Engine: Processing task [{task_id}] ({intent})."
                )

                # 🔄 UPGRADE: Dynamically select the best tool pipeline!
                suggested_tool = await self._resolve_execution_tool(intent, context)
                task_data["suggested_tool"] = suggested_tool
                
                self.logger.info(
                    f"🎯 Pipeline choice for [{intent}]: Selected '{suggested_tool}'"
                )

                # Hand the task off to the planner!
                bus.publish(
                    "request_planning", # Planner listens to this to generate execution steps
                    data=task_data,
                    source="decision_engine",
                )

                self.task_queue.task_done()

            except asyncio.CancelledError:
                break
            except Exception as e:
                self.logger.error(
                    f"Error in Decision Engine queue processor: {e}"
                )
                await asyncio.sleep(1)


# Global instance
decision_engine = DecisionEngine()