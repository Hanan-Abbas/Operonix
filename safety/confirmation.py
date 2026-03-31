import asyncio
import logging
import time
from core.event_bus import bus


class ConfirmationManager:
    """🛑 The Human-in-the-Loop bridge for the AI OS.

    Intercepts high-risk tasks, pauses execution, and manages the state of
    pending user approvals from the dashboard or voice system.
    """

    def __init__(self):
        self.logger = logging.getLogger("ConfirmationManager")

        # In-memory storage for tasks waiting for user approval
        # Structure: { task_id: { "task_data": {...}, "timestamp": 12345,
        # "status": "pending" } }
        self.pending_confirmations = {}

        # Auto-expire confirmations after 5 minutes to prevent memory leaks
        self.timeout_seconds = 300

    async def start(self):
        """Subscribe to events requiring human interaction."""
        # Listen for the safety validator yelling "Hold on, I need a human!"
        bus.subscribe("confirmation_required", self.hold_for_confirmation)

        # Listen for the user's response coming from the API / Dashboard
        bus.subscribe("user_response_received", self.handle_user_response)

        # Start background task to clean up expired requests
        asyncio.create_task(self._cleanup_expired_requests())

        self.logger.info(
            "🛑 Confirmation Manager: Online. Standing by for high-risk operations."
        )

    async def hold_for_confirmation(self, event):
        """Intercepts a high-risk task and holds it in a pending state."""
        data = event.data
        task_id = data.get("task_id")
        reason = data.get("reason")

        self.logger.warning(
            f"⏸️ Task [{task_id}] paused. Reason: {reason}. Awaiting user approval."
        )

        # Store the task and its data so we can resume it later
        self.pending_confirmations[task_id] = {
            "task_data": data.get(
                "step_data"
            ),  # The specific step being executed
            "full_task_data": data,  # The whole event payload
            "timestamp": time.time(),
            "status": "pending",
            "reason": reason,
        }

        # Broadcast to the dashboard / websocket that the user needs to look at something
        bus.publish(
            "ui_prompt_required",
            {
                "task_id": task_id,
                "prompt_type": "high_risk_confirmation",
                "message": f"The AI wants to perform a high-risk action: {reason}",
                "actions": ["allow", "deny"],
            },
            source="confirmation_manager",
        )

    