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

        # ── REFLECTOR INTEGRATION ──────────────────────────────────────────
        # The Reflector publishes "evolution_needed" when a plugin tier has
        # accumulated enough consecutive failures to warrant structural
        # improvement (not just a retry). This is distinct from
        # "plugin_evolution_requested" which is fired by plugin_health_monitor
        # based on a success-rate threshold.
        #
        # The Reflector signal carries intent, capability, failure_category,
        # and a root_cause string — richer context than the health monitor's
        # performance-based signal. We use it to evolve the specific plugin
        # that handles the failing intent rather than the generic capability
        # tier name.
        #
        # RISK: evolution_needed may arrive before a plugin for the intent
        # even exists (capability_missing category). In that case, _on_evolution_needed
        # detects the missing plugin and delegates to capability_gap_detector
        # (via a "capability_gap_detected" publish) rather than trying to
        # evolve a non-existent plugin.
        bus.subscribe("evolution_needed", self._on_evolution_needed_reflector)

        self.logger.info("🧬 Plugin Evolver: Online.")

    # ── Event Handlers ─────────────────────────────────────────────────────────

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

    async def _on_evolution_needed_reflector(self, event):
        """
        Handles the "evolution_needed" signal from brain.Reflector.

        The Reflector provides richer context than plugin_health_monitor:
        it includes failure_category, root_cause, and suggested_fix which
        are injected into the evolution prompt for a more targeted improvement.

        Flow:
          1. capability_missing → no plugin exists yet. Delegate to generator
             pipeline via "capability_gap_detected" event.
          2. Plugin exists for this intent → call evolve() with Reflector's
             root_cause as the reason so the LLM gets targeted context.
          3. Non-plugin tier (api/command/ui) → skip; evolver only handles
             plugin.py files.

        RISK MITIGATIONS:
          R1 — Fully wrapped in try/except; bad payload never crashes the loop.
          R2 — Plugin registry lookup guarded with try/except; skips if not
               populated yet.
          R3 — capability_missing delegation avoids double-trigger via the
               existing cooldown logic in _trigger_gap().
          R4 — Non-plugin tiers are explicitly skipped.
        """
        try:
            data             = event.data or {}
            intent           = data.get("intent", "")
            capability       = data.get("capability", "")
            failure_category = data.get("failure_category", "")
            root_cause       = data.get("root_cause", "Reflector-detected degradation")
            suggested_fix    = data.get("suggested_fix", "")

            if not intent:
                return

            # R4 — only act on plugin-tier failures
            if capability and capability not in ("plugin", "unknown", ""):
                self.logger.debug(
                    "evolution_needed skipped: capability='%s' is not plugin tier.",
                    capability,
                )
                return

            # Case 1: no plugin exists yet — delegate to gap detector (R3)
            if failure_category == "capability_missing":
                self.logger.info(
                    "⚡ Reflector: capability_missing for '%s' — "
                    "delegating to capability_gap_detected pipeline.", intent,
                )
                bus.publish(
                    "capability_gap_detected",
                    {
                        "intent":              intent,
                        "reason":              root_cause,
                        "consecutive_failures": data.get("consecutive_failures", 1),
                        "window_failures":      0,
                        "failure_summary":      {},
                        "source":              "reflector_via_evolver",
                    },
                    source="plugin_evolver",
                )
                return

            # Case 2: find the installed plugin that handles this intent (R2)
            plugin_name: str | None = None
            try:
                for entry_name, entry in plugin_registry.entries.items():
                    caps = getattr(entry.manifest, "capabilities", []) or []
                    caps_norm   = [str(c).lower().replace("_", " ") for c in caps]
                    intent_norm = intent.lower().replace("_", " ")
                    mf_intent   = (entry.manifest.intent or "").lower()
                    if intent_norm in caps_norm or mf_intent == intent.lower():
                        plugin_name = entry_name
                        break
            except Exception as lookup_exc:
                self.logger.debug(
                    "Plugin registry lookup for intent='%s' failed: %s",
                    intent, lookup_exc,
                )

            if not plugin_name:
                self.logger.debug(
                    "evolution_needed: no installed plugin for intent='%s' — skipping.",
                    intent,
                )
                return

            if plugin_name in self._evolving:
                self.logger.debug(
                    "Evolution already in progress for '%s'. Skipping Reflector trigger.",
                    plugin_name,
                )
                return

            # Build a rich reason string for the LLM evolution prompt
            reason_parts = [f"Reflector: {root_cause}"]
            if suggested_fix:
                reason_parts.append(f"Suggested: {suggested_fix}")
            reason = " | ".join(reason_parts)

            self.logger.info(
                "⚡ Reflector triggered evolution for plugin='%s' intent='%s'",
                plugin_name, intent,
            )

            self._evolving.add(plugin_name)
            try:
                await self.evolve(plugin_name=plugin_name, intent=intent, reason=reason)
            finally:
                self._evolving.discard(plugin_name)

        except Exception as exc:
            self.logger.warning(
                "_on_evolution_needed_reflector failed (non-fatal): %s", exc
            )  # R1

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
                category=template_engine.get_category(intent),
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
        Asks the LLM to propose an improved plugin strategy.

        Uses separator-based plain-text format (not JSON) to avoid Groq
        JSON-mode truncation of multi-line code strings — the same fix
        applied to generator.py's _generate_code().
        """
        history_text = ""
        if evolution_history:
            history_text = "EVOLUTION HISTORY:\n" + "\n".join(
                f"  v{h['from_version']}→v{h['to_version']}: {h['reason']}"
                for h in evolution_history[-3:]
            )

        # Detect category from the current code's header comment or intent
        category = template_engine.get_category(intent)
        pattern_library = template_engine.get_pattern_library()

        prompt = f"""You are an expert Python developer evolving a plugin for a self-evolving AI OS.
The plugin is underperforming. Improve its implementation strategy.

PLUGIN NAME: {plugin_name}
INTENT: {intent}
PLUGIN CATEGORY: {category}
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

PATTERN LIBRARY — working code patterns to use in your evolved implementation:
{pattern_library}

CURRENT PLUGIN CODE (what is failing):
```python
{current_code}
```

CURRENT TESTS:
```python
{current_tests[:1000] if current_tests else "No existing tests"}
```

EVOLUTION TASK:
1. Identify WHY the current implementation is failing based on the error patterns
2. Propose a DIFFERENT, better strategy using the pattern library above
3. Do NOT just fix syntax errors — improve the core approach
4. Bump the version to {new_version} in the class attribute
5. Do NOT use capability_registry for automation — use pyautogui/keyboard directly
6. For background/loop tasks: use threading.Event + daemon threads (see pattern library)

One sentence rationale of what changed, then output EXACTLY this format:

===RATIONALE===
<one sentence explaining the core change>
===PLUGIN_CODE===
<complete improved plugin.py content>
===TEST_CODE===
<complete improved test_plugin.py content>
===END===
"""
        try:
            raw = await llm_client.generate(prompt, use_json=False)

            plugin_code = ""
            tests = ""
            rationale = ""

            if raw and isinstance(raw, str):
                # Parse separator format
                import re as _re
                rat_m  = _re.search(r"===RATIONALE===\s*(.*?)\s*===PLUGIN_CODE===", raw, _re.DOTALL)
                code_m = _re.search(r"===PLUGIN_CODE===\s*(.*?)\s*===TEST_CODE===", raw, _re.DOTALL)
                test_m = _re.search(r"===TEST_CODE===\s*(.*?)\s*===END===", raw, _re.DOTALL)

                if rat_m:
                    rationale = rat_m.group(1).strip()
                if code_m:
                    plugin_code = code_m.group(1).strip()
                if test_m:
                    tests = test_m.group(1).strip()

                # Fallback: try JSON dict if LLM ignored the format
                if not plugin_code:
                    try:
                        import json as _json
                        parsed = _json.loads(raw)
                        plugin_code = parsed.get("plugin_code", "")
                        tests       = parsed.get("tests", "")
                        rationale   = parsed.get("evolution_rationale", "")
                    except Exception:
                        pass

                # Strip code fences if present
                from plugins.generator import PluginGenerator
                plugin_code = PluginGenerator._strip_code_fences(plugin_code)
                tests       = PluginGenerator._strip_code_fences(tests)

            elif isinstance(raw, dict):
                # LLM returned JSON despite use_json=False
                from plugins.generator import PluginGenerator
                plugin_code = PluginGenerator._strip_code_fences(raw.get("plugin_code", ""))
                tests       = PluginGenerator._strip_code_fences(raw.get("tests", ""))
                rationale   = raw.get("evolution_rationale", "")

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