"""
core/metrics.py

System-wide performance metrics for Operonix.

REFLECTOR INTEGRATION:
  Added reflection-specific counters and a capability_confidence snapshot
  dict so the dashboard API (/api/health, /api/metrics) can surface
  Reflector activity without coupling to memory.long_term_memory at
  query time.

  Fields added:
    reflections_total     — total reflect() calls (success + failure)
    reflections_failed    — reflect() calls where Reflector itself errored
    evolution_triggers    — times evolution_needed was published
    capability_confidence — last-known per-tier confidence snapshot
                            updated by Reflector._update_confidence()

  RISK MITIGATIONS:
    R1 — All new fields have safe zero/empty defaults so existing code
         that constructs SystemMetrics() with no args continues to work.
    R2 — capability_confidence is a plain dict (not LongTermMemory) so
         reading it for the dashboard never does disk I/O.
    R3 — snapshot_confidence() is idempotent and safe to call from any
         thread; it only writes to a plain dict under the dataclass.
"""
from __future__ import annotations

from dataclasses import dataclass, field
from typing import Dict
import time


@dataclass
class SystemMetrics:
    """Track agent performance metrics."""
    total_tasks: int = 0
    successful_tasks: int = 0
    failed_tasks: int = 0

    total_duration_seconds: float = 0.0
    task_count_by_intent: Dict[str, int] = field(default_factory=dict)

    stt_attempts: int = 0
    stt_confidence_sum: float = 0.0

    uptime_seconds: float = field(default_factory=lambda: time.time())

    # ── Reflector metrics (R1 — zero/empty defaults) ──────────────────────
    reflections_total:     int              = 0
    reflections_failed:    int              = 0
    evolution_triggers:    int              = 0
    # Last-known per-tier confidence snapshot written by Reflector (R2, R3)
    # Keys: "plugin", "api", "command", "ui"  Values: float 0.0–1.0
    capability_confidence: Dict[str, float] = field(default_factory=dict)

    # ── Derived helpers ───────────────────────────────────────────────────

    def avg_task_duration(self) -> float:
        """Average task duration in seconds."""
        if self.total_tasks == 0:
            return 0.0
        return self.total_duration_seconds / self.total_tasks

    def success_rate(self) -> float:
        """Task success rate (0–100)."""
        if self.total_tasks == 0:
            return 0.0
        return (self.successful_tasks / self.total_tasks) * 100

    def avg_stt_confidence(self) -> float:
        """Average STT confidence (0–1)."""
        if self.stt_attempts == 0:
            return 0.0
        return self.stt_confidence_sum / self.stt_attempts

    def reflection_failure_rate(self) -> float:
        """
        Fraction of reflect() calls where the Reflector itself errored (0–100).

        A non-zero value here signals a problem in the Reflector's LLM or
        memory layer, not in the tasks being reflected on.

        RISK R1 — returns 0.0 when no reflections have run yet.
        """
        if self.reflections_total == 0:
            return 0.0
        return (self.reflections_failed / self.reflections_total) * 100

    def snapshot_confidence(self, tier: str, score: float) -> None:
        """
        Update the in-memory capability confidence snapshot for a tier.

        Called by brain.Reflector._update_confidence() after every persist
        so the dashboard API can serve current scores without a DB read.

        Parameters
        ----------
        tier  : capability tier name e.g. "plugin", "api", "command", "ui"
        score : clamped float [0.0, 1.0]

        RISK R3 — plain dict write; safe from any thread, no locking needed
        because Python's GIL makes single dict[key]=value assignments atomic.
        """
        self.capability_confidence[tier] = round(score, 4)

    def to_dict(self) -> dict:
        """
        Serialise all metrics to a plain dict for the health API endpoint.

        Includes all Reflector fields so /api/metrics returns the complete
        picture without additional imports.
        """
        return {
            "total_tasks":          self.total_tasks,
            "successful_tasks":     self.successful_tasks,
            "failed_tasks":         self.failed_tasks,
            "success_rate":         round(self.success_rate(), 2),
            "avg_task_duration_s":  round(self.avg_task_duration(), 3),
            "total_duration_s":     round(self.total_duration_seconds, 3),
            "task_count_by_intent": self.task_count_by_intent,
            "stt_attempts":         self.stt_attempts,
            "avg_stt_confidence":   round(self.avg_stt_confidence(), 3),
            "uptime_s":             round(time.time() - self.uptime_seconds, 1),
            # Reflector fields
            "reflections_total":       self.reflections_total,
            "reflections_failed":      self.reflections_failed,
            "reflection_failure_rate": round(self.reflection_failure_rate(), 2),
            "evolution_triggers":      self.evolution_triggers,
            "capability_confidence":   self.capability_confidence,
        }


metrics = SystemMetrics()