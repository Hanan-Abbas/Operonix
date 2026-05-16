"""
executor/retry_manager.py
──────────────────────────
Retry and backoff manager for Operonix executor.

CHANGES FROM PREVIOUS VERSION
──────────────────────────────
RISK 5 FIX — should_retry() consumed an attempt slot even when the caller
    did not actually retry (e.g. the pre-waterfall capability block called
    should_retry() then returned immediately).  This meant the waterfall had
    one fewer retry available than configured for that step.
    FIX: Added peek_should_retry() — a read-only eligibility check that
    inspects state without mutating it and without sleeping.  The pre-waterfall
    block uses peek_should_retry() to decide whether a retryable error should
    loop back through capability_registry.  Only the waterfall loop (which
    actually does retry) calls the mutating should_retry().
"""
import asyncio
import logging
from core.config import settings
from core.event_bus import bus


class RetryManager:

    def __init__(self, max_retries=None):
        self.logger = logging.getLogger("RetryManager")

        self.max_retries = (
            max_retries
            if max_retries is not None
            else getattr(settings, "MAX_RETRY_ATTEMPTS", 3)
        )

        self.attempts: dict[str, dict[int, int]] = {}   # {task_id: {step_index: count}}
        self.total_attempts: dict[str, int] = {}         # {task_id: count}

        # Errors that are never worth retrying — structural / logic failures
        self.non_retryable_errors: set[str] = {
            "permission_denied",
            "invalid_input",
            "not_supported",
            "not_found",
        }

    # ── Read-only eligibility check (RISK 5 FIX) ─────────────────────────────

    def peek_should_retry(
        self,
        task_id: str,
        step_index: int,
        error_type: str | None = None,
        max_retries: int | None = None,
    ) -> bool:
        """
        Check retry eligibility WITHOUT consuming an attempt slot or sleeping.

        Use this when you need to decide whether to loop back through a
        retryable path (e.g. the pre-waterfall capability block) but you don't
        want to burn an attempt counter or introduce a backoff delay at the
        decision point.  The actual mutating should_retry() is called later
        by the code that performs the real retry.

        Returns True if a retry would be allowed, False otherwise.
        Does NOT publish any bus events.
        """
        if error_type in self.non_retryable_errors:
            return False

        current_count = (
            self.attempts.get(task_id, {}).get(step_index, 0)
        )
        limit = max_retries or self.max_retries
        return current_count < limit

    # ── Mutating retry gate (increments counter, sleeps) ─────────────────────

    async def should_retry(
        self,
        task_id: str,
        step_index: int,
        error_type: str | None = None,
        max_retries: int | None = None,
    ) -> bool:
        """
        Determine if a failed step should be retried AND consume one attempt slot.

        Applies exponential backoff (0.2 s, 0.4 s, 0.8 s …).
        Only call this when you are about to actually retry the step — not for
        a speculative eligibility check (use peek_should_retry() for that).
        """
        if error_type in self.non_retryable_errors:
            bus.publish(
                "retry_skipped",
                {"task_id": task_id, "reason": error_type},
                source="retry_manager",
            )
            return False

        if task_id not in self.attempts:
            self.attempts[task_id] = {}
            self.total_attempts[task_id] = 0

        current_count = self.attempts[task_id].get(step_index, 0)
        limit = max_retries or self.max_retries

        if current_count < limit:
            self.attempts[task_id][step_index] = current_count + 1
            self.total_attempts[task_id] += 1

            delay = 0.2 * (2 ** current_count)

            self.logger.info(
                "Task [%s] step %d failed. Retrying in %.1fs... "
                "(Attempt %d/%d)",
                task_id, step_index, delay, current_count + 1, limit,
            )

            await asyncio.sleep(delay)

            bus.publish(
                "retry_attempt",
                {
                    "task_id": task_id,
                    "step":    step_index,
                    "attempt": current_count + 1,
                    "delay":   delay,
                },
                source="retry_manager",
            )
            return True

        bus.publish(
            "retry_failed",
            {"task_id": task_id, "step": step_index},
            source="retry_manager",
        )
        return False

    def clear_task(self, task_id: str) -> None:
        """Clean up attempt counters after a task completes or is aborted."""
        self.attempts.pop(task_id, None)
        self.total_attempts.pop(task_id, None)


# Global instance
retry_manager = RetryManager()