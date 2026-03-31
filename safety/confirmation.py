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

    