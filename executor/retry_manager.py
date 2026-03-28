import asyncio
from core.event_bus import bus

class RetryManager:
    def __init__(self, max_retries=3):
        self.max_retries = max_retries
        self.attempts = {}  # {task_id: {step_index: count}}
        self.total_attempts = {}  # {task_id: count}

        self.non_retryable_errors = {
            "permission_denied",
            "invalid_input",
            "not_supported"
        }

    async def should_retry(self, task_id, step_index, error_type=None, max_retries=None):
        if error_type in self.non_retryable_errors:
            bus.emit("retry_skipped", {
                "task_id": task_id,
                "reason": error_type
            })
            return False

        if task_id not in self.attempts:
            self.attempts[task_id] = {}
            self.total_attempts[task_id] = 0

        current_count = self.attempts[task_id].get(step_index, 0)
        limit = max_retries or self.max_retries

        if current_count < limit:
            self.attempts[task_id][step_index] = current_count + 1
            self.total_attempts[task_id] += 1

            # Exponential backoff
            delay = 0.2 * (2 ** current_count)
            await asyncio.sleep(delay)

            bus.emit("retry_attempt", {
                "task_id": task_id,
                "step": step_index,
                "attempt": current_count + 1,
                "delay": delay
            })

            return True

        bus.emit("retry_failed", {
            "task_id": task_id,
            "step": step_index
        })

        return False

    def clear_task(self, task_id):
        self.attempts.pop(task_id, None)
        self.total_attempts.pop(task_id, None)
