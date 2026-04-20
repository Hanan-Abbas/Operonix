"""
plugins/plugin_health_monitor.py

Monitors plugin execution health in real-time.

Subscribes to:
  - plugin_execution_completed  (fired by executor when a plugin runs)
  - plugin_execution_failed

Responsibilities:
  - Update plugin run stats in plugin_registry and plugin_memory
  - Detect health degradation (falling success rate)
  - Revoke trust if plugin fails beyond threshold
  - Trigger plugin_evolver when performance degrades
  - Emit structured JSON observability logs
"""
from __future__ import annotations

import json
import logging
import time
from datetime import datetime

from core.config import settings
from core.event_bus import bus
from plugins.manifest_schema import PluginStatus
from plugins.plugin_memory import plugin_memory
from plugins.registry import plugin_registry

logger = logging.getLogger("PluginHealthMonitor")

# Health thresholds
REVOKE_CONSECUTIVE_FAILURES: int = int(
    getattr(settings, "PLUGIN_REVOKE_CONSECUTIVE", 5)
)
DEGRADE_SUCCESS_RATE_THRESHOLD: float = float(
    getattr(settings, "PLUGIN_DEGRADE_THRESHOLD", 0.60)
)
MIN_RUNS_FOR_HEALTH_CHECK: int = int(
    getattr(settings, "PLUGIN_MIN_RUNS", 5)
)
EVOLVE_SUCCESS_RATE_THRESHOLD: float = float(
    getattr(settings, "PLUGIN_EVOLVE_THRESHOLD", 0.75)
)


class PluginHealthMonitor:
    """
    Real-time plugin health tracking with automatic degradation response.

    Health state machine per plugin:
      TRUSTED → degrading   → trigger evolver
      TRUSTED → failing     → revoke trust
      TRUSTED → timeout loop → revoke trust
    """

    def __init__(self):
        self.logger = logging.getLogger("PluginHealthMonitor")

    async def start(self):
        """Subscribe to plugin execution events."""
        bus.subscribe("plugin_execution_completed", self._on_execution_completed)
        bus.subscribe("plugin_execution_failed",    self._on_execution_failed)
        self.logger.info("💓 Plugin Health Monitor: Active.")

    # ── Event Handlers ─────────────────────────────────────────────────────────

    async def _on_execution_completed(self, event):
        """Fired when a plugin run returns any result (success or error status)."""
        data        = event.data or {}
        plugin_name = data.get("plugin_name", "")
        status      = data.get("status", "error")   # "success" | "error" | "timeout"
        elapsed_ms  = int(data.get("elapsed_ms", 0))
        intent      = data.get("intent", "")
        error       = data.get("error")

        if not plugin_name:
            return

        success = (status == "success")

        # Update registry stats
        plugin_registry.increment_stats(plugin_name, success)

        # Persist to plugin_memory
        plugin_memory.record_run(
            plugin_name=plugin_name,
            intent=intent,
            status=status,
            elapsed_ms=elapsed_ms,
            error=error,
        )

        # Emit structured observability log
        self._emit_obs_log(plugin_name, status, elapsed_ms, intent, error)

        # Run health assessment
        await self._assess_health(plugin_name, intent)

    async def _on_execution_failed(self, event):
        """Fires for hard failures (exception, timeout) during plugin execution."""
        data        = event.data or {}
        plugin_name = data.get("plugin_name", "")
        error       = data.get("error", "Unknown failure")
        elapsed_ms  = int(data.get("elapsed_ms", 0))
        intent      = data.get("intent", "")
        timed_out   = data.get("timed_out", False)

        if not plugin_name:
            return

        status = "timeout" if timed_out else "error"

        plugin_registry.increment_stats(plugin_name, success=False)
        plugin_memory.record_run(
            plugin_name=plugin_name,
            intent=intent,
            status=status,
            elapsed_ms=elapsed_ms,
            error=error,
        )

        self._emit_obs_log(plugin_name, status, elapsed_ms, intent, error)
        await self._assess_health(plugin_name, intent)

    # ── Health Assessment ─────────────────────────────────────────────────────

    async def _assess_health(self, plugin_name: str, intent: str):
        """
        Evaluates plugin health and triggers the appropriate response.

        Decision tree:
          consecutive_failures >= REVOKE threshold → revoke trust
          success_rate < EVOLVE threshold (enough runs) → request evolution
          success_rate < DEGRADE threshold → warn only
        """
        summary = plugin_memory.get_performance_summary(plugin_name, last_n=20)

        if summary["total_runs"] < MIN_RUNS_FOR_HEALTH_CHECK:
            return  # Not enough data yet

        consecutive = summary["consecutive_failures"]
        success_rate = summary["success_rate"]

        # ── Hard failure: revoke trust ────────────────────────────────────────
        if consecutive >= REVOKE_CONSECUTIVE_FAILURES:
            self.logger.error(
                f"🚨 Plugin '{plugin_name}' has {consecutive} consecutive failures. "
                f"Revoking trust."
            )
            plugin_registry.revoke_trust(
                plugin_name,
                reason=f"{consecutive} consecutive failures. "
                       f"Errors: {summary['recent_errors'][:2]}",
            )
            bus.publish(
                "plugin_health_critical",
                {
                    "plugin_name": plugin_name,
                    "action": "trust_revoked",
                    "consecutive_failures": consecutive,
                    "intent": intent,
                },
                source="plugin_health_monitor",
            )
            return

        # ── Degrading: trigger evolver ────────────────────────────────────────
        if success_rate < EVOLVE_SUCCESS_RATE_THRESHOLD:
            self.logger.warning(
                f"⚠️ Plugin '{plugin_name}' success rate dropped to "
                f"{success_rate:.0%}. Requesting evolution."
            )
            bus.publish(
                "plugin_evolution_requested",
                {
                    "name": plugin_name,
                    "intent": intent,
                    "reason": (
                        f"Success rate degraded to {success_rate:.0%} "
                        f"(threshold: {EVOLVE_SUCCESS_RATE_THRESHOLD:.0%})"
                    ),
                    "performance_summary": summary,
                },
                source="plugin_health_monitor",
            )
            return

        # ── Warning: log but don't act yet ───────────────────────────────────
        if success_rate < DEGRADE_SUCCESS_RATE_THRESHOLD:
            self.logger.warning(
                f"📉 Plugin '{plugin_name}' showing degraded performance: "
                f"{success_rate:.0%} success rate."
            )

    # ── Observability ─────────────────────────────────────────────────────────

    def _emit_obs_log(
        self,
        plugin_name: str,
        status: str,
        elapsed_ms: int,
        intent: str,
        error: str | None,
    ):
        """
        Emits a structured JSON observability log for every plugin execution.
        Consumed by dashboard/live_logs.js or external log aggregators.
        """
        log_entry = {
            "timestamp": datetime.utcnow().isoformat(),
            "type": "plugin_execution",
            "plugin_name": plugin_name,
            "intent": intent,
            "status": status,
            "elapsed_ms": elapsed_ms,
            "error": error,
        }
        # Log as structured JSON
        self.logger.info(json.dumps(log_entry))

        # Also publish to event bus for dashboard consumption
        bus.publish(
            "plugin_obs_log",
            log_entry,
            source="plugin_health_monitor",
        )

    def get_health_report(self, plugin_name: str) -> dict:
        """
        Public API for dashboard queries.
        Returns full health report for a plugin.
        """
        summary = plugin_memory.get_performance_summary(plugin_name)
        entry   = plugin_registry.get(plugin_name)

        return {
            "plugin_name": plugin_name,
            "status": entry.status.value if entry else "unknown",
            "trusted": entry.manifest.trusted if entry else False,
            "performance": summary,
            "health": self._compute_health_label(summary),
        }

    @staticmethod
    def _compute_health_label(summary: dict) -> str:
        """Returns human-readable health label."""
        rate = summary.get("success_rate", 0)
        consec = summary.get("consecutive_failures", 0)

        if consec >= REVOKE_CONSECUTIVE_FAILURES:
            return "critical"
        if rate >= 0.90:
            return "healthy"
        if rate >= EVOLVE_SUCCESS_RATE_THRESHOLD:
            return "degrading"
        return "unhealthy"


# Global instance
plugin_health_monitor = PluginHealthMonitor()