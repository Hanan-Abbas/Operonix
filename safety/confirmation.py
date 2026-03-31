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

    async def handle_user_response(self, event):
        """Processes the user's 'Allow' or 'Deny' input from the dashboard."""
        data = event.data
        task_id = data.get("task_id")
        user_choice = data.get(
            "choice"
        ).lower()  # Expected: 'allow' or 'deny'

        if task_id not in self.pending_confirmations:
            self.logger.warning(
                f"Received response for unknown or expired task: {task_id}"
            )
            return

        saved_data = self.pending_confirmations.pop(task_id)

        if user_choice == "allow":
            self.logger.info(
                f"🟢 User APPROVED task [{task_id}]. Resuming execution..."
            )

            # Re-publish the original task data to let the executor take over!
            bus.publish(
                "task_safety_cleared",
                saved_data["full_task_data"],
                source="confirmation_manager",
            )

        elif user_choice == "deny":
            self.logger.warning(
                f"🔴 User DENIED task [{task_id}]. Aborting execution."
            )

            # Tell the system the task failed due to user intervention
            bus.publish(
                "task_failed",
                {
                    "task_id": task_id,
                    "error": "Operation denied by user.",
                    "stage": "confirmation",
                },
                source="confirmation_manager",
            )

    async def _cleanup_expired_requests(self):
        """Background loop to clear out confirmations that the user ignored."""
        while True:
            await asyncio.sleep(60)  # Check every minute
            current_time = time.time()
            expired_ids = []

            for task_id, data in self.pending_confirmations.items():
                if current_time - data["timestamp"] > self.timeout_seconds:
                    expired_ids.append(task_id)

            for task_id in expired_ids:
                self.logger.warning(
                    f"⏰ Confirmation for task [{task_id}] expired due to inactivity."
                )
                self.pending_confirmations.pop(task_id)

                # Fail the task automatically if the user ignores it
                bus.publish(
                    "task_failed",
                    {
                        "task_id": task_id,
                        "error": "Confirmation timeout. Action auto-denied for safety.",
                        "stage": "confirmation",
                    },
                    source="confirmation_manager",
                )


# Global instance
confirmation_manager = ConfirmationManager()