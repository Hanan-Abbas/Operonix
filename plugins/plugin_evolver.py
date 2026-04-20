"""
plugins/plugin_evolver.py

Upgrades existing plugins when their performance degrades.

This is what makes the system SELF-EVOLVING rather than just self-healing:
  - Instead of fixing broken code (like debugging/auto_fix.py does),
    the evolver improves the plugin's strategy based on what it has learned
  - It reads failure patterns from plugin_memory and episodic_memory
  - It asks DeepSeek to propose a better strategy
  - It validates, sandboxes, and deploys the evolved version
  - Old version is backed up via plugin_rollback before overwrite

Subscribes to: plugin_evolution_requested
Publishes:     plugin_evolved, plugin_evolution_failed
"""
from __future__ import annotations

import logging
import os
from datetime import datetime

from brain.llm_client import llm_client
from core.config import settings
from core.event_bus import bus
from memory.episodic import episodic_memory
from plugins.manifest_schema import PluginStatus
from plugins.plugin_memory import plugin_memory
from plugins.plugin_rollback import plugin_rollback
from plugins.registry import plugin_registry
from plugins.sandbox_runner import sandbox_runner
from plugins.template_engine import template_engine

logger = logging.getLogger("PluginEvolver")

MAX_EVOLVE_ATTEMPTS: int = int(getattr(settings, "MAX_RETRY_ATTEMPTS", 3))
PLUGINS_INSTALLED_DIR = os.path.join(
    str(getattr(settings, "PLUGINS_DIR", "plugins")), "installed"
)


class PluginEvolver:
    """
    Upgrades plugins whose performance has degraded.

    Triggered by plugin_health_monitor when success_rate drops below threshold.
    """

    def __init__(self):
        self.logger = logging.getLogger("PluginEvolver")
        self._evolving: set[str] = set()

    async def start(self):
        """Subscribe to evolution requests."""
        bus.subscribe("plugin_evolution_requested", self._on_evolution_requested)
        self.logger.info("🧬 Plugin Evolver: Online.")

    # ── Event Handler ─────────────────────────────────────────────────────────

    async def _on_evolution_requested(self, event):
        data        = event.data or {}
        plugin_name = data.get("name", "")
        intent      = data.get("intent", "")
        reason      = data.get("reason", "Performance degradation")

        if not plugin_name:
            return

        if plugin_name in self._evolving:
            self.logger.debug(
                f"Evolution already in progress for '{plugin_name}'. Skipping."
            )
            return

        self._evolving.add(plugin_name)
        try:
            await self.evolve(plugin_name=plugin_name, intent=intent, reason=reason)
        finally:
            self._evolving.discard(plugin_name)

    # ── Core Evolution Pipeline ────────────────────────────────────────────────

    async def evolve(
        self, plugin_name: str, intent: str, reason: str = ""
    ) -> bool:
        """
        Evolves a plugin to a better version.

        Returns True if evolution succeeded and new version is deployed.
        """
        self.logger.info(
            f"🧬 Starting evolution for plugin '{plugin_name}': {reason}"
        )

        plugin_dir = os.path.join(PLUGINS_INSTALLED_DIR, plugin_name)
        plugin_file = os.path.join(plugin_dir, "plugin.py")
        test_dir    = os.path.join(plugin_dir, "tests")
        test_file   = os.path.join(test_dir, "test_plugin.py")

        if not os.path.exists(plugin_file):
            self.logger.error(
                f"Cannot evolve '{plugin_name}': plugin.py not found at {plugin_file}"
            )
            return False

        # Read current code
        with open(plugin_file, "r") as f:
            current_code = f.read()

        current_tests = ""
        if os.path.exists(test_file):
            with open(test_file, "r") as f:
                current_tests = f.read()

        # Get current manifest for version tracking
        entry = plugin_registry.get(plugin_name)
        old_version = entry.manifest.version if entry else "1.0"
        new_version = self._bump_version(old_version)

        # Read performance summary
        perf_summary = plugin_memory.get_performance_summary(plugin_name)
        failure_summary = episodic_memory.get_failure_summary(intent or plugin_name)
        evolution_history = plugin_memory.get_evolution_history(plugin_name)

        # Create backup before any changes
        snapshot = plugin_rollback.create_snapshot(plugin_dir, plugin_name)

        # Generate evolved version
        for attempt in range(MAX_EVOLVE_ATTEMPTS):
            self.logger.info(
                f"🔬 Evolution attempt {attempt + 1}/{MAX_EVOLVE_ATTEMPTS} "
                f"for '{plugin_name}'"
            )

            evolved_code, evolved_tests = await self._generate_evolved_code(
                plugin_name=plugin_name,
                intent=intent,
                current_code=current_code,
                current_tests=current_tests,
                perf_summary=perf_summary,
                failure_summary=failure_summary,
                evolution_history=evolution_history,
                reason=reason,
                new_version=new_version,
            )

            if not evolved_code:
                self.logger.warning(f"Evolution attempt {attempt + 1} produced no code.")
                continue

            # Quick sandbox test before full pipeline
            evolved_file = plugin_file + ".evolved_tmp"
            with open(evolved_file, "w") as f:
                f.write(evolved_code)

            quick_result = await sandbox_runner.quick_sandbox_test(
                plugin_path=evolved_file,
                context={"active_window": "test"},
                args={},
            )
            os.unlink(evolved_file)

            if quick_result.get("status") != "success":
                self.logger.warning(
                    f"Quick sandbox test failed: {quick_result.get('error')}"
                )
                continue

            # Run full pipeline on evolved code
            pipeline_report = await sandbox_runner.run_full_pipeline(
                plugin_name=plugin_name,
                plugin_code=evolved_code,
                test_code=evolved_tests or current_tests,
                plugin_dir=plugin_dir,
                intent=intent,
                failure_summary=failure_summary,
            )

            if pipeline_report["passed"]:
                # Deploy the evolved version
                self._deploy_evolved(
                    plugin_dir=plugin_dir,
                    plugin_name=plugin_name,
                    evolved_code=evolved_code,
                    evolved_tests=evolved_tests or current_tests,
                    old_version=old_version,
                    new_version=new_version,
                    reason=reason,
                )

                # Clean up backup since evolution succeeded
                plugin_rollback.delete_backups(snapshot)

                self.logger.info(
                    f"🎉 Plugin '{plugin_name}' successfully evolved to v{new_version}."
                )

                bus.publish(
                    "plugin_evolved",
                    {
                        "name": plugin_name,
                        "intent": intent,
                        "from_version": old_version,
                        "to_version": new_version,
                        "reason": reason,
                    },
                    source="plugin_evolver",
                )
                return True

            self.logger.warning(
                f"Evolution attempt {attempt + 1} failed pipeline. "
                f"Stage: {pipeline_report.get('stage_failed')}"
            )

        # All attempts failed — rollback
        self.logger.error(
            f"🚨 All evolution attempts failed for '{plugin_name}'. Rolling back."
        )
        plugin_rollback.restore_snapshot(plugin_dir, snapshot)

        bus.publish(
            "plugin_evolution_failed",
            {
                "name": plugin_name,
                "intent": intent,
                "reason": "All evolution attempts exhausted",
            },
            source="plugin_evolver",
        )
        return False

    def _deploy_evolved(
        self,
        plugin_dir: str,
        plugin_name: str,
        evolved_code: str,
        evolved_tests: str,
        old_version: str,
        new_version: str,
        reason: str,
    ):
        """Write evolved files, update manifest, record in plugin_memory."""
        plugin_file = os.path.join(plugin_dir, "plugin.py")
        test_file   = os.path.join(plugin_dir, "tests", "test_plugin.py")
        os.makedirs(os.path.dirname(test_file), exist_ok=True)

        with open(plugin_file, "w") as f:
            f.write(evolved_code)
        with open(test_file, "w") as f:
            f.write(evolved_tests)

        # Record version in manifest
        plugin_rollback.record_version_in_manifest(
            plugin_dir, old_version, new_version, reason
        )

        # Record in plugin_memory
        plugin_memory.record_evolution(
            plugin_name=plugin_name,
            from_version=old_version,
            to_version=new_version,
            reason=reason,
        )

        # Hot-reload the evolved plugin
        bus.publish(
            "plugin_reload_requested",
            {"name": plugin_name},
            source="plugin_evolver",
        )

    # ── LLM Evolution Prompt ───────────────────────────────────────────────────

    async def _generate_evolved_code(
        self,
        plugin_name: str,
        intent: str,
        current_code: str,
        current_tests: str,
        perf_summary: dict,
        failure_summary: dict,
        evolution_history: list,
        reason: str,
        new_version: str,
    ) -> tuple[str, str]:
        """
        Asks DeepSeek to propose an improved plugin strategy.
        """
        history_text = ""
        if evolution_history:
            history_text = "EVOLUTION HISTORY:\n" + "\n".join(
                f"  v{h['from_version']}→v{h['to_version']}: {h['reason']}"
                for h in evolution_history[-3:]  # Last 3 evolutions
            )

        prompt = f"""
You are an expert Python developer evolving a plugin for a self-evolving AI OS.
The plugin is underperforming. Improve its implementation strategy.

PLUGIN NAME: {plugin_name}
INTENT: {intent}
EVOLUTION REASON: {reason}
NEW VERSION: {new_version}

PERFORMANCE SUMMARY:
- Success rate: {perf_summary.get('success_rate', 0):.0%}
- Total runs: {perf_summary.get('total_runs', 0)}
- Consecutive failures: {perf_summary.get('consecutive_failures', 0)}
- Recent errors: {perf_summary.get('recent_errors', [])}

FAILURE PATTERNS (from episodic memory):
- Common reasons: {failure_summary.get('common_reasons', [])}

{history_text}

CURRENT PLUGIN CODE:
```python
{current_code}
```

CURRENT TESTS:
```python
{current_tests[:1000] if current_tests else "No existing tests"}
```

EVOLUTION TASK:
1. Identify WHY the current implementation is failing based on the error patterns
2. Propose a DIFFERENT, better strategy to handle the intent "{intent}"
3. Do NOT just fix syntax errors — improve the core approach
4. Bump the version to {new_version} in the class attribute
5. Keep all BasePlugin rules (registry access only, no direct imports)

Return ONLY valid JSON:
{{
    "plugin_code": "<improved plugin.py content>",
    "tests": "<improved test_plugin.py content>",
    "evolution_rationale": "<one paragraph explaining what changed and why>"
}}
"""
        try:
            result = await llm_client.generate(prompt, use_json=True)
            if isinstance(result, dict):
                from plugins.generator import PluginGenerator
                plugin_code = PluginGenerator._strip_code_fences(
                    result.get("plugin_code", "")
                )
                tests = PluginGenerator._strip_code_fences(
                    result.get("tests", "")
                )
                rationale = result.get("evolution_rationale", "")
                if rationale:
                    self.logger.info(f"🧬 Evolution rationale: {rationale}")
                return plugin_code, tests
        except Exception as e:
            self.logger.error(f"Evolution code generation failed: {e}")

        return "", ""

    @staticmethod
    def _bump_version(version: str) -> str:
        """Increments the minor version number."""
        try:
            parts = version.split(".")
            minor = int(parts[-1]) + 1
            parts[-1] = str(minor)
            return ".".join(parts)
        except Exception:
            return "2.0"


# Global instance
plugin_evolver = PluginEvolver()