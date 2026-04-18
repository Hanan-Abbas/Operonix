"""
executor/execution_tracker.py

Tracks per-task execution metrics (start time, method used, step count).
Emits `task_completed` enriched with `method_used` and `intent` so the
orchestrator's `finalize_task` can relay them to the panel history row.

This module is used by executor.py internally — no external callers needed.
"""
from __future__ import annotations

import logging
import time
from dataclasses import dataclass, field
from typing import Any

log = logging.getLogger("ExecutionTracker")


@dataclass
class TaskRecord:
    task_id: str
    intent: str | None
    started_at: float = field(default_factory=time.monotonic)
    step_count: int = 0
    steps_completed: int = 0
    method_used: str = "unknown"
    failed: bool = False
    error: str | None = None

    @property
    def elapsed_ms(self) -> int:
        return int((time.monotonic() - self.started_at) * 1000)


class ExecutionTracker:
    """
    Lightweight in-process store for active task records.

    The executor calls:
        tracker.begin(task_id, intent, step_count)
        tracker.record_step(task_id, method_used)
        tracker.complete(task_id)   # → returns TaskRecord
        tracker.fail(task_id, error)
    """

    def __init__(self) -> None:
        self._records: dict[str, TaskRecord] = {}

    def begin(self, task_id: str, intent: str | None, step_count: int = 0) -> TaskRecord:
        record = TaskRecord(task_id=task_id, intent=intent, step_count=step_count)
        self._records[task_id] = record
        log.debug("ExecutionTracker: began task [%s] intent=%s", task_id, intent)
        return record

    def record_step(self, task_id: str, method_used: str) -> None:
        record = self._records.get(task_id)
        if record:
            record.steps_completed += 1
            record.method_used = method_used

    def complete(self, task_id: str) -> TaskRecord | None:
        record = self._records.pop(task_id, None)
        if record:
            log.debug(
                "ExecutionTracker: completed [%s] method=%s elapsed=%dms",
                task_id, record.method_used, record.elapsed_ms,
            )
        return record

    def fail(self, task_id: str, error: str) -> TaskRecord | None:
        record = self._records.pop(task_id, None)
        if record:
            record.failed = True
            record.error = error
            log.debug(
                "ExecutionTracker: failed [%s] error=%s elapsed=%dms",
                task_id, error, record.elapsed_ms,
            )
        return record

    def get(self, task_id: str) -> TaskRecord | None:
        return self._records.get(task_id)

    def active_count(self) -> int:
        return len(self._records)


# Global instance used by executor.py
execution_tracker = ExecutionTracker()