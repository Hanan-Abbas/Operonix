import asyncio
import logging
from core.config import settings  # <-- IMPORT CONFIG
from core.event_bus import bus


class RetryManager:

    def __init__(self, max_retries=None):
        self.logger = logging.getLogger("RetryManager")

        # Use the value from core/config.py if no specific override is passed
        self.max_retries = (
            max_retries
            if max_retries is not None
            else getattr(settings, "MAX_RETRY_ATTEMPTS", 3)
        )

        self.attempts = {}  # {task_id: {step_index: count}}
        self.total_attempts = {}  # {task_id: count}

        # Don't waste time retrying operations that failed due to pure logic or lack of access
        self.non_retryable_errors = {
            "permission_denied",
            "invalid_input",
            "not_supported",
            "not_found",  # Added to prevent searching for ghost files repeatedly
        }

    async def should_retry(
        self, task_id, step_index, error_type=None, max_retries=None
    ):
        """Determines if a failed step should be attempted again.

        Applies exponential backoff to give the system time to recover.
        """
        if error_type in self.non_retryable_errors:
            # Using publish for background queue processing
            bus.publish(
                "retry_skipped",
                {"task_id": task_id, "reason": error_type},
                source="retry_manager",
            )
            return False

        # Initialize tracking for the task if missing
        if task_id not in self.attempts:
            self.attempts[task_id] = {}
            self.total_attempts[task_id] = 0

        # Safely extract current count for this specific step
        current_count = self.attempts[task_id].get(step_index, 0)
        limit = max_retries or self.max_retries

        if current_count < limit:
            # Update the counts
            self.attempts[task_id][step_index] = current_count + 1
            self.total_attempts[task_id] += 1

            # Exponential backoff: 0.2s, 0.4s, 0.8s...
            delay = 0.2 * (2**current_count)

            self.logger.info(
                f"Task [{task_id}] step {step_index} failed. Retrying in {delay:.1f}s... (Attempt {current_count + 1}/{limit})"
            )

            # Wait before returning True to hold the execution loop
            await asyncio.sleep(delay)

            bus.publish(
                "retry_attempt",
                {
                    "task_id": task_id,
                    "step": step_index,
                    "attempt": current_count + 1,
                    "delay": delay,
                },
                source="retry_manager",
            )

            return True

        # If we reached the limit
        bus.publish(
            "retry_failed",
            {"task_id": task_id, "step": step_index},
            source="retry_manager",
        )

        return False

    def clear_task(self, task_id):
        """Cleans memory up after a task is completed or permanently

        aborted.
        """
        self.attempts.pop(task_id, None)
        self.total_attempts.pop(task_id, None)


# Global instance
retry_manager = RetryManager()