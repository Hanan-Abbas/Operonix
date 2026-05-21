"""
plugins/generator.py

The core generation engine of the self-evolving plugin system.

Pipeline (triggered by "capability_gap_detected" event):
  1. Check if a similar plugin already exists (vector_store similarity)
  2. Build generation prompt from failure context + template skeleton
  3. Generate plugin code + tests via DeepSeek (llm_client.generate)
  4. Run full sandbox pipeline (sandbox_runner)
  5. If pipeline fails → apply Gemini critique feedback → retry (max 3x)
  6. If all passes → write to installedgenerator/ → request user approval
  7. On approval → loader hot-reloads → capability registeredGenerate

Structured output expected from LLM:
{
    "plugin_code": "...",
    "tests": "...",
    "metadata": {
        "name": "...",
        "description": "...",
        "permissions": [],
        "risk_level": "low|medium|high",
        "capabilities": ["<intent strings this plugin handles>"]
    }
}
"""
from __future__ import annotations

import asyncio
import logging
import os
import re
from datetime import datetime

from brain.llm_client import llm_client
from core.config import settings
from core.event_bus import bus
from memory.episodic import episodic_memory
from plugins.manifest_schema import (
    PluginManifest, PluginStatus, RiskLevel, validate_manifest_dict
)
from plugins.sandbox_runner import sandbox_runner
from plugins.template_engine import template_engine

# Auto-approve and deploy low-risk plugins without waiting for user.
# Set AUTO_APPROVE_PLUGINS=False in .env to require manual approval.
AUTO_APPROVE_PLUGINS: bool = bool(getattr(settings, "AUTO_APPROVE_PLUGINS", True))
AUTO_APPROVE_RISK_LEVELS: set[str] = {"low"}  # Only auto-approve low-risk

logger = logging.getLogger("PluginGenerator")

MAX_GENERATION_ATTEMPTS: int = int(getattr(settings, "MAX_RETRY_ATTEMPTS", 3))
# Maximum LLM critique calls per generation attempt (guards against TPM exhaustion
# when multiple cloud providers cascade through 429s on the same request burst).
MAX_LLM_CRITIQUE_CALLS_PER_ATTEMPT: int = 1
PLUGINS_INSTALLED_DIR = os.path.join(
    str(getattr(settings, "PLUGINS_DIR", "plugins")), "installed"
)


class PluginGenerator:
    """
    Generates new plugins from detected capability gaps.

    Subscribes to: capability_gap_detected
    Publishes:     plugin_ready_for_approval, plugin_generation_failed
    """

    def __init__(self):
        self.logger = logging.getLogger("PluginGenerator")
        # Track active generation tasks to avoid duplicate generation
        self._generating: set[str] = set()

    async def start(self):
        """Subscribe to gap detection events."""
        bus.subscribe("capability_gap_detected", self._on_gap_detected)
        bus.subscribe("plugin_approved",          self._on_plugin_approved)
        bus.subscribe("plugin_rejected",          self._on_plugin_rejected)
        self.logger.info("🏭 Plugin Generator: Online.")

    # ── Event Handlers ─────────────────────────────────────────────────────────

    async def _on_gap_detected(self, event):
        """Triggered when capability_gap_detector fires a gap."""
        data   = event.data or {}
        intent = data.get("intent", "")

        if not intent:
            return

        # Prevent duplicate concurrent generation for the same intent
        if intent in self._generating:
            self.logger.debug(f"Generation already in progress for '{intent}'. Skipping.")
            return

        self._generating.add(intent)
        try:
            await self.generate_plugin_for_intent(
                intent=intent,
                failure_summary=data.get("failure_summary", {}),
                reason=data.get("reason", ""),
            )
        finally:
            self._generating.discard(intent)

    async def _on_plugin_approved(self, event):
        """
        User has approved a pending plugin — hot-reload it and record
        a persistent success rule so future generation of this category
        starts with the winning pattern.
        """
        data        = event.data or {}
        plugin_name = data.get("name", "")
        plugin_dir  = data.get("plugin_dir", "")
        intent      = data.get("intent", "")

        if not plugin_name:
            return

        self.logger.info(f"✅ Plugin '{plugin_name}' approved by user. Hot-reloading...")

        try:
            from plugins.manifest_schema import PluginStatus
            from plugins.registry import plugin_registry
            from plugins.loader import plugin_loader

            # Mark as trusted in manifest
            plugin_registry.update_status(
                plugin_name, PluginStatus.TRUSTED, plugin_dir=plugin_dir
            )

            # Hot-reload into capability_registry
            await plugin_loader.hot_reload(plugin_name)

            bus.publish(
                "plugin_deployed",
                {"name": plugin_name, "intent": intent},
                source="plugin_generator",
            )
            self.logger.info(f"🚀 Plugin '{plugin_name}' is now live.")

            # ── Notify capability_mapper so it indexes the new vectors ────────
            # Without this the mapper's capability_vectors dict has no entry
            # for the new plugin's capabilities, so the next matching intent
            # scores below threshold, fires mapping_failed, and gap_detector
            # re-triggers generation for an intent that is already handled.
            _entry = plugin_registry.get(plugin_name)
            _caps  = (
                list(getattr(_entry.manifest, "capabilities", []))
                if _entry else [intent]
            )
            bus.publish(
                "capability_registered",
                {
                    "name":         plugin_name,
                    "intent":       intent,
                    "capabilities": _caps,
                    "source":       "plugin",
                },
                source="plugin_generator",
            )

        except Exception as e:
            self.logger.error(f"Failed to deploy approved plugin '{plugin_name}': {e}")
            return

        # ── Persistent approval learning ──────────────────────────────────────
        # Write a success episode to episodic memory so get_failure_summary()
        # resets the consecutive_failures counter for this intent.
        # Also write a generation rule to learner's override_counts so the
        # category gets an implicit "approved" signal for future ranking.
        try:
            await episodic_memory.record_episode(
                task_id=f"plugin_approved:{plugin_name}",
                intent=intent,
                steps=[{"action": f"plugin_generation:{plugin_name}"}],
                outcome="success",
                metadata={"plugin_name": plugin_name, "approved_by": "user"},
            )
        except Exception as exc:
            self.logger.debug("Could not record approval episode: %s", exc)

        try:
            category = template_engine.get_category(intent) if intent else "generic"
            # _learn_from_override expects an event — build a minimal surrogate
            class _FakeEvent:
                def __init__(self, d): self.data = d
            await learner._learn_from_override(_FakeEvent({
                "app":            "*",          # cross-app signal
                "intent":         intent,
                "chosen_method":  f"plugin:{category}",
                "default_method": "",
            }))
        except Exception as exc:
            self.logger.debug("Could not write approval rule to learner: %s", exc)

    # ── Core Generation Pipeline ───────────────────────────────────────────────

    async def generate_plugin_for_intent(
        self,
        intent: str,
        failure_summary: dict | None = None,
        reason: str = "",
    ) -> bool:
        """
        Full generation pipeline for a single intent.
        Returns True if plugin was successfully generated and is awaiting approval.
        """
        self.logger.info(f"🏭 Starting plugin generation for intent: '{intent}'")

        # Step 1: Check if a similar plugin already exists
        existing = await self._check_existing_plugin(intent)
        if existing:
            self.logger.info(
                f"♻️ Similar plugin '{existing}' found for intent '{intent}'. "
                f"Triggering evolution instead of generating new."
            )
            bus.publish(
                "plugin_evolution_requested",
                {"name": existing, "intent": intent, "reason": reason},
                source="plugin_generator",
            )
            return True

        # Step 2: Derive plugin name from intent
        plugin_name = self._intent_to_plugin_name(intent)
        plugin_dir  = os.path.join(PLUGINS_INSTALLED_DIR, plugin_name)
        os.makedirs(plugin_dir, exist_ok=True)

        # Step 3: Build prompt context
        failure_context = failure_summary or episodic_memory.get_failure_summary(intent)
        category = template_engine.get_category(intent)
        skeleton = template_engine.get_plugin_skeleton(
            plugin_name=plugin_name,
            intent=intent,
            description=f"Auto-generated plugin to handle: {intent}",
            category=category,
        )
        test_skeleton = template_engine.get_test_skeleton(plugin_name, intent, category=category)

        # Step 4: Generation + retry loop
        for attempt in range(MAX_GENERATION_ATTEMPTS):
            self.logger.info(
                f"⚡ Generation attempt {attempt + 1}/{MAX_GENERATION_ATTEMPTS} "
                f"for '{plugin_name}'"
            )

            # Generate plugin code via DeepSeek
            plugin_code, tests, metadata = await self._generate_code(
                intent=intent,
                plugin_name=plugin_name,
                skeleton=skeleton,
                test_skeleton=test_skeleton,
                failure_context=failure_context,
            )

            if not plugin_code:
                self.logger.warning(f"Generation attempt {attempt + 1} produced no code.")
                continue

            # Sanitize test patch targets before running the pipeline.
            # LLMs frequently generate  patch("plugin.MagicMock")  which is not
            # a valid patch path — MagicMock lives in unittest.mock, not in the
            # plugin module.  Fix all such targets so pytest doesn't fail with
            # a confusing "cannot import name 'MagicMock' from 'plugin'" error.
            if tests:
                tests = self._sanitize_test_patch_targets(tests, plugin_name)

            # Run full validation pipeline
            pipeline_report = await sandbox_runner.run_full_pipeline(
                plugin_name=plugin_name,
                plugin_code=plugin_code,
                test_code=tests,
                plugin_dir=plugin_dir,
                intent=intent,
                failure_summary=failure_context,
                category=category,
            )

            if pipeline_report["passed"]:
                # Write manifest and register pending plugin
                await self._write_and_register(
                    plugin_name=plugin_name,
                    plugin_dir=plugin_dir,
                    plugin_code=plugin_code,
                    tests=tests,
                    metadata=metadata,
                    intent=intent,
                )
                return True

            # Pipeline failed — enrich failure_summary with stage feedback
            # for the next prompt. Keep skeleton CLEAN (no comment injection)
            # because Python comments embedded in JSON strings break the parser.
            stage       = pipeline_report.get("stage_failed", "unknown")
            tweaks      = pipeline_report.get("llm_audit", {}).get("suggested_tweaks", "")
            test_output = pipeline_report.get("pytest", {}).get("output", "")

            self.logger.warning(
                f"Pipeline failed at stage '{stage}' for '{plugin_name}'. "
                f"Applying feedback and retrying..."
            )

            # ── Inter-attempt backoff (TPM guard) ─────────────────────────────
            # Each failed attempt triggers LLM critique calls.  Back off before
            # the next attempt so provider token-per-minute windows can refill.
            # Base: 12s (enough for Groq's 12k TPM window to clear by ~60%).
            # Cap:  60s on the final retry.
            if attempt < MAX_GENERATION_ATTEMPTS - 1:
                backoff_s = min(12 * (2 ** attempt), 60)
                self.logger.info(
                    f"⏳ Backing off {backoff_s}s before attempt "
                    f"{attempt + 2}/{MAX_GENERATION_ATTEMPTS} to protect TPM limits."
                )
                await asyncio.sleep(backoff_s)

            # Feed failure context back into the PROMPT (not the skeleton)
            failure_summary = self._build_feedback_context(
                failure_summary, stage, tweaks, test_output
            )

        # All attempts exhausted
        self.logger.error(
            f"🚨 All {MAX_GENERATION_ATTEMPTS} generation attempts failed for '{intent}'."
        )
        bus.publish(
            "plugin_generation_failed",
            {"intent": intent, "plugin_name": plugin_name},
            source="plugin_generator",
        )
        return False

    # ── LLM Code Generation ────────────────────────────────────────────────────

    async def _generate_code(
        self,
        intent: str,
        plugin_name: str,
        skeleton: str,
        test_skeleton: str,
        failure_context: dict,
    ) -> tuple[str, str, dict]:
        """
        Calls the LLM to generate plugin code and tests.
        Returns (plugin_code, test_code, metadata_dict).

        Uses a two-call approach:
          Call 1 (no JSON mode) — generates the full plugin.py and test code
                                   as plain text with clear separators.
                                   Plain text avoids Groq JSON mode truncation
                                   of multi-line code strings.
          Call 2 (JSON mode)   — generates only the small metadata dict.
        """
        common_reasons = failure_context.get("common_reasons", [])
        prev_attempts  = failure_context.get("previous_attempts", [])

        # Load permanent cross-session trust rules for this category.
        # Written by sandbox_runner when AST catches a blocking call.
        # Injected into the prompt so the LLM sees them on EVERY attempt,
        # including first attempts after a system restart.
        trust_rules = self._load_trust_rules_for_category(category)

        # ── Call 1: Generate code as plain text (no JSON mode) ────────────────
        # Embedding code in JSON causes Groq to escape newlines and truncate
        # method bodies, making the validator think no methods are implemented.
        #
        # NOTE: The skeleton already contains a LOCKED run() and validate()
        # contract. The LLM writes ONLY the body of _execute(). This is
        # enforced structurally by the skeleton and by Rule 2 below.
        code_prompt = (
            "You are an expert Python developer for a self-evolving AI OS agent.\n"
            "Generate a production-quality plugin implementation.\n"
            "\n"
            f"TARGET OS: {TARGET_OS}  ← Write ALL shell commands and paths for THIS OS ONLY.\n"
            f"OS NOTES:  {OS_SHELL_NOTE}\n"
            "\n"
            f'FAILING INTENT: "{intent}"\n'
            f"PLUGIN CATEGORY: {category}\n"
            f"CONSECUTIVE FAILURES: {failure_context.get('consecutive_failures', 0)}\n"
            f"COMMON FAILURE REASONS: {common_reasons}\n"
            f"PREVIOUS ATTEMPT FAILURES: {prev_attempts}\n"
            f"{trust_rules}\n"
            "\n"
            "PATTERN LIBRARY — working code patterns for common tasks, copy what you need:\n"
            f"{template_engine.get_pattern_library()}\n"
            "\n"
            "PLUGIN SKELETON — implement the body of _execute() ONLY:\n"
            "```python\n"
            f"{skeleton}\n"
            "```\n"
            "\n"
            "CRITICAL RULES:\n"
            "1.  The plugin class MUST subclass BasePlugin.\n"
            "2.  STRICT SCOPE ISOLATION: You are ONLY allowed to write code inside\n"
            "    the body of the _execute() method. Do NOT rewrite, modify, rename,\n"
            "    or add arguments to run(), validate(), or any other pre-written method.\n"
            "    The run() contract is locked by the framework — modifying it causes\n"
            "    immediate validation rejection.\n"
            "3.  Do NOT add sys.path manipulation or from __future__ imports.\n"
            "4.  ALWAYS import asyncio at the top if you use await anywhere.\n"
            "5.  _execute() must return the type the skeleton specifies:\n"
            "    - automation category: return a list of strings (step log)\n"
            "    - all other categories: return a dict (the result payload)\n"
            "6.  Access ALL services via: capability_registry.get('service_name').\n"
            "7.  NEVER import from automation/, context/, core/, or safety/ directly.\n"
            "8.  ALL exceptions must be caught inside _execute(); return the error\n"
            "    payload as specified in the skeleton. If a shell command fails,\n"
            "    ALWAYS include stderr in the error message.\n"
            "9.  NO BLOCKING OS CALLS. Use asyncio.create_subprocess_shell with\n"
            "    asyncio.wait_for(timeout=30). NEVER use subprocess.run(),\n"
            "    subprocess.call(), subprocess.Popen(), or os.system().\n"
            "10. stdout of run() must be clean JSON only. Do NOT print() anything\n"
            "    inside _execute() or run() — use self.logger.debug() instead.\n"
            "\n"
            "Output ONLY the complete plugin.py source. No other text.\n"
            "\n"
            "===PLUGIN_CODE===\n"
            "<complete plugin.py source code here>\n"
            "===END===\n"
        )
        # ── Call 2: Metadata only (small JSON, no code) ───────────────────────
        meta_prompt = f"""Return ONLY valid JSON metadata for a plugin named '{plugin_name}'.
No markdown, no explanation, just the JSON object.

The plugin handles intent: "{intent}"

Rules:
- risk_level: "low" for read/open/display actions, "medium" for write/create, "high" for delete/shell
- capabilities: all intent strings this plugin should match (include synonyms)
- parameter_schema: list every arg the plugin's validate() method requires or accepts
  Each entry: {{"name": "<arg>", "required": true|false, "aliases": ["<other_names>"]}}
  aliases = other names the LLM or planner might use for the same value
- intent_patterns: how to detect when a misclassified LLM intent should be rerouted here
  Each entry: {{"raw_intents": ["run_command","open","launch"], "command_is_verb": true, "args_is_target": true}}
  command_is_verb=true means: the LLM put a verb like "open"/"launch" in the command field
  args_is_target=true means: the real target value is in args[0] positional list
  Leave intent_patterns as [] if no rerouting is needed.

{{
    "name": "{plugin_name}",
    "description": "<one sentence: what this plugin does>",
    "permissions": ["ui_interaction"],
    "risk_level": "low",
    "capabilities": ["{intent}"],
    "parameter_schema": [
        {{"name": "<primary_arg_name>", "required": true, "aliases": ["<alias1>", "<alias2>"]}}
    ],
    "intent_patterns": []
}}
"""
        try:
            # ── Call 1: Plugin code only (no tests) ───────────────────────────
            # Tests are generated in Call 3 AFTER we have the real plugin code.
            # Generating them together causes the LLM to write tests for what
            # it *intended* to write, not what it *actually* wrote — leading to
            # wrong mock paths and missing edge cases.
            raw_text = await llm_client.generate(code_prompt, use_json=False)

            plugin_code = ""
            tests = ""

            if raw_text and isinstance(raw_text, str):
                plugin_code, _bundled_tests = self._parse_separator_response(raw_text)
                # Discard _bundled_tests — we regenerate from the real code below
            elif isinstance(raw_text, dict):
                # Fallback: LLM returned JSON despite use_json=False
                plugin_code = self._strip_code_fences(raw_text.get("plugin_code", ""))

            # ── Call 2: Metadata JSON ─────────────────────────────────────────
            # Small call (~300 tokens). Runs while we still have the plugin code
            # in context so Call 3 doesn't need to re-read it.
            metadata = {}
            try:
                meta_result = await llm_client.generate(meta_prompt, use_json=True)
                if isinstance(meta_result, dict) and "name" in meta_result:
                    metadata = meta_result
                    # Cross-check: high-risk shell plugins should require admin
                    if (
                        metadata.get("risk_level") == "high"
                        and not metadata.get("requires_admin")
                        and TARGET_OS != "windows"
                    ):
                        metadata["requires_admin"] = True
            except Exception as me:
                self.logger.debug(f"Metadata generation failed (non-fatal): {me}")

            if not metadata:
                metadata = {
                    "name":             plugin_name,
                    "description":      f"Auto-generated plugin for: {intent}",
                    "permissions":      [],
                    "risk_level":       "low",
                    "requires_admin":   False,
                    "os_compatibility": [TARGET_OS],
                    "capabilities":     [intent],
                }

            # ── Call 3: Test generation from actual plugin code ───────────────
            # This call sees the *real* plugin_code that was generated, not a
            # skeleton. The LLM cannot get mock paths wrong because the real
            # import and attribute names are right there in the code it reads.
            # Token budget: ~800 tokens (test file is smaller than plugin).
            if plugin_code:
                tests = await self._generate_tests_from_code(
                    plugin_code=plugin_code,
                    plugin_name=plugin_name,
                    intent=intent,
                    category=category,
                    test_skeleton=test_skeleton,
                )
            else:
                tests = ""

            snippet = (plugin_code or "")[:200].replace("\n", "↵")
            self.logger.debug(f"Generated code snippet: {snippet}")

            return plugin_code, tests, metadata

        except Exception as e:
            self.logger.error(f"LLM generation failed: {e}")

        return "", "", {}

    # ── Post-Generation ────────────────────────────────────────────────────────

    async def _generate_tests_from_code(
        self,
        plugin_code: str,
        plugin_name: str,
        intent: str,
        category: str,
        test_skeleton: str,
    ) -> str:
        """
        Call 3 — Generate tests by reading the ACTUAL plugin code.

        Why separate from _generate_code:
          - The LLM sees real method names, real imports, real attribute paths
          - Cannot hallucinate mock patch targets that don't exist
          - Cannot write assertions for methods that weren't implemented
          - Produces tests that match what the sandbox will actually execute

        Token budget: ~800 tokens (test files are ~40% of plugin size).
        """
        # Retrieve category-specific failure patterns from episodic memory
        # so the test prompt warns about previously seen test failures.
        test_failure_context = ""
        try:
            from memory.episodic import episodic_memory
            summary = episodic_memory.get_failure_summary(intent)
            pytest_failures = [
                r for r in summary.get("common_reasons", [])
                if "pytest" in r.lower() or "test" in r.lower() or "mock" in r.lower()
            ]
            if pytest_failures:
                test_failure_context = (
                    f"\nPREVIOUS TEST FAILURES FOR THIS INTENT (avoid repeating these):\n"
                    + "\n".join(f"  - {r}" for r in pytest_failures[:3])
                )
        except Exception:
            pass

        test_prompt = (
            "You are an expert Python test engineer for an AI OS agent.\n"
            "Write a complete pytest test file for the plugin below.\n"
            "\n"
            f"TARGET OS: {TARGET_OS}\n"
            f"PLUGIN NAME: {plugin_name}\n"
            f"INTENT: {intent}\n"
            f"CATEGORY: {category}\n"
            f"{test_failure_context}\n"
            "\n"
            "ACTUAL PLUGIN CODE (read this carefully — write tests FOR THIS CODE):\n"
            "```python\n"
            f"{plugin_code}\n"
            "```\n"
            "\n"
            "TEST SKELETON (use as structure guide, fill with real tests):\n"
            "```python\n"
            f"{test_skeleton}\n"
            "```\n"
            "\n"
            "CRITICAL TEST RULES:\n"
            "1.  Import the plugin using: from plugin import <ClassName>\n"
            "    The class name is visible in the ACTUAL PLUGIN CODE above.\n"
            "2.  ALL OS-level side effects MUST be mocked. This includes:\n"
            "    file I/O, shell commands, network, process signals, clipboard.\n"
            "3.  When mocking asyncio.create_subprocess_shell, patch it exactly\n"
            "    as it is USED in the plugin — check the actual import path:\n"
            "    - If plugin does 'import asyncio': patch('plugin.asyncio.create_subprocess_shell')\n"
            "    - If plugin does 'from asyncio import create_subprocess_shell': patch('plugin.create_subprocess_shell')\n"
            "    NEVER patch 'plugin.MagicMock', 'plugin.AsyncMock', or 'plugin.patch'.\n"
            "4.  For async mocks use AsyncMock for coroutine returns:\n"
            "        mock_proc.communicate = AsyncMock(return_value=(b'out', b''))\n"
            "5.  Use @pytest.mark.asyncio + async def for tests that call async methods.\n"
            "    Use asyncio.run() for sync test functions calling async plugin methods.\n"
            "6.  Use single braces for dicts: {'key': 'value'} NOT {{'key': 'value'}}\n"
            "7.  Every test must assert on 'status' key in the result.\n"
            "8.  Include: one structural test, one success path, one error/bad-args path.\n"
            "9.  Tests must run standalone with pytest — no external services needed.\n"
            "10. Do NOT test private methods (_execute, _run_cmd) directly — test run().\n"
            "\n"
            "Output ONLY the complete test_plugin.py source. No other text.\n"
        )

        try:
            raw = await llm_client.generate(test_prompt, use_json=False)
            if raw and isinstance(raw, str):
                return self._strip_code_fences(raw.strip())
        except Exception as e:
            self.logger.warning(f"Test generation (Call 3) failed: {e} — using skeleton")

        # Fallback: return the skeleton so sandbox still has something to run
        return test_skeleton

    async def _write_and_register(
        self,
        plugin_name: str,
        plugin_dir: str,
        plugin_code: str,
        tests: str,
        metadata: dict,
        intent: str,
    ):
        """Writes the plugin files and creates a PENDING manifest."""
        # plugin.py and test file already written by sandbox_runner
        # Write the manifest
        # Derive requires_confirmation from risk_level — low-risk plugins
        # never need confirmation; the manifest is the single source of truth.
        _risk_str = metadata.get("risk_level", "medium")
        _requires_confirm = _risk_str in ("medium", "high")

        manifest = PluginManifest(
            name=plugin_name,
            description=metadata.get("description", f"Plugin for {intent}"),
            intent=intent,
            version="1.0",
            capabilities=metadata.get("capabilities", [intent]),
            permissions=metadata.get("permissions", []),
            risk_level=RiskLevel(_risk_str),
            status=PluginStatus.PENDING,
            trusted=False,
            requires_confirmation=_requires_confirm,
            tags=[intent, "auto-generated"],
            # Self-describing contract — consumed by intent_parser and planner
            # so they never need hardcoded rules for this plugin.
            parameter_schema=metadata.get("parameter_schema", []),
            intent_patterns=metadata.get("intent_patterns", []),
        )
        manifest.save(plugin_dir)

        # Save to plugin_memory for future similarity lookups
        try:
            from plugins.plugin_memory import plugin_memory
            await plugin_memory.save_to_vector_store(
                plugin_name, manifest.description, intent
            )
        except Exception as e:
            self.logger.debug(f"Could not index in vector store: {e}")

        self.logger.info(
            f"📦 Plugin '{plugin_name}' written to {plugin_dir}. "
            f"Awaiting user approval."
        )

        # Auto-approve low-risk plugins if enabled — agent learns immediately.
        # High/medium risk plugins still require manual confirmation.
        risk = manifest.risk_level.value
        if AUTO_APPROVE_PLUGINS and risk in AUTO_APPROVE_RISK_LEVELS:
            self.logger.info(
                f"🤖 Auto-approving low-risk plugin '{plugin_name}' — deploying now."
            )
            bus.publish(
                "plugin_approved",
                {
                    "name":       plugin_name,
                    "intent":     intent,
                    "plugin_dir": plugin_dir,
                },
                source="plugin_generator",
            )
        else:
            # Publish approval request for medium/high risk plugins
            bus.publish(
                "confirmation_required",
                {
                    "type":        "plugin_approval",
                    "name":        plugin_name,
                    "intent":      intent,
                    "plugin_dir":  plugin_dir,
                    "description": manifest.description,
                    "risk_level":  risk,
                    "reason": (
                        f"New plugin '{plugin_name}' generated for intent '{intent}'. "
                        f"Risk: {risk}. Review and approve to deploy."
                    ),
                },
                source="plugin_generator",
            )

    async def _check_existing_plugin(self, intent: str) -> str | None:
        """
        Check plugin_memory for a similar existing plugin.

        IMPORTANT: Always verify the plugin actually exists on disk before
        returning it. The vector store may contain entries for plugins that
        were cleaned up after failed generation runs. Returning a stale name
        causes generator to fire plugin_evolution_requested for a non-existent
        plugin, which the evolver immediately rejects with "plugin.py not found".
        """
        try:
            from plugins.plugin_memory import plugin_memory
            result = await plugin_memory.find_similar_plugin(intent)
            if result:
                plugin_name = result.get("plugin_name")
                if not plugin_name:
                    return None
                # Verify plugin.py actually exists — vector store entries
                # survive cleanup of failed generation dirs
                plugin_file = os.path.join(
                    PLUGINS_INSTALLED_DIR, plugin_name, "plugin.py"
                )
                if not os.path.exists(plugin_file):
                    self.logger.debug(
                        f"Vector store found '{plugin_name}' for intent '{intent}' "
                        f"but plugin.py is missing on disk — generating fresh."
                    )
                    return None
                return plugin_name
        except Exception:
            pass
        return None

    # ── Helpers ───────────────────────────────────────────────────────────────

    def _apply_generation_feedback(
        self, skeleton: str, stage: str, tweaks: str, test_output: str
    ) -> str:
        """
        FIX: Previously prepended Python comments to the skeleton code.
        Those comments got embedded as strings inside the JSON payload,
        causing Groq's json_validate_failed because special characters
        (quotes, backslashes, newlines) in the error text broke JSON.

        Now returns the skeleton UNCHANGED. Feedback travels in the
        _build_generation_prompt() failure_context dict as plain text,
        not inside the code string.
        """
        # Skeleton stays clean — feedback is in the prompt text, not the code
        return skeleton

    def _build_feedback_context(
        self, base_context: dict, stage: str, tweaks: str, test_output: str
    ) -> dict:
        """Build an enriched failure context dict for the retry prompt."""
        enriched = dict(base_context)
        prev_failures = enriched.get("previous_attempts", [])
        prev_failures.append({
            "stage":       stage,
            "tweaks":      tweaks,
            "test_output": (test_output or "")[:300],
        })
        enriched["previous_attempts"] = prev_failures
        return enriched

    @staticmethod
    def _sanitize_test_patch_targets(test_code: str, plugin_name: str) -> str:
        """
        Fix incorrect patch() target paths in LLM-generated test code.

        LLMs frequently write:
            patch("plugin.MagicMock")
            patch("plugin.AsyncMock")
            patch("plugin.patch")

        These are invalid — MagicMock/AsyncMock live in unittest.mock, not in
        the plugin module.  pytest fails immediately with:
            AttributeError: <module 'plugin'> does not have the attribute 'MagicMock'

        Rules applied:
        1. patch("plugin.MagicMock")  → removed (MagicMock is the patch utility
           itself; you never patch MagicMock — you use it as the replacement).
        2. patch("plugin.AsyncMock")  → same removal.
        3. patch("plugin.patch")      → same removal.
        4. patch("plugin.X") where X is an all-caps constant or stdlib name
           that clearly doesn't live in the plugin → warn and leave unchanged
           (the LLM retry will fix it with the error context).
        5. Ensures 'from unittest.mock import MagicMock, AsyncMock, patch'
           is present when any of those names are used in the test file.
        """
        import re

        # ── 1. Remove nonsensical patch("plugin.MagicMock/AsyncMock/patch") ──
        # These lines are always wrong and cause immediate pytest failure.
        # They usually appear as:  with patch("plugin.MagicMock") as mock_bus:
        invalid_targets = {"MagicMock", "AsyncMock", "patch", "Mock"}
        lines = test_code.splitlines(keepends=True)
        cleaned_lines = []
        skip_with_block = False
        for line in lines:
            # Detect:  with patch("plugin.<invalid>") ...  or  @patch("plugin.<invalid>")
            m = re.search(
                r'(?:with\s+|@)patch\s*\(\s*["\']plugin\.(' +
                '|'.join(invalid_targets) +
                r')["\']',
                line,
            )
            if m:
                target = m.group(1)
                # Replace entire line with a comment explaining the fix
                indent = len(line) - len(line.lstrip())
                cleaned_lines.append(
                    " " * indent +
                    f"# REMOVED: patch(\"plugin.{target}\") is invalid — "
                    f"{target} is not an attribute of the plugin module.\n"
                )
                # If it's a 'with' statement, mark that the block body should
                # remain but the context manager is gone — Python requires the
                # body to stay valid, so we convert to a no-op context.
                if "with patch" in line:
                    skip_with_block = True
                continue
            # If we removed a 'with patch(...)' line, replace the ' as mock_X:'
            # tail (already consumed above) — nothing more needed; body follows.
            skip_with_block = False
            cleaned_lines.append(line)

        test_code = "".join(cleaned_lines)

        # ── 2. Ensure unittest.mock imports cover what the test uses ──────────
        mock_names_used = set(re.findall(
            r'\b(MagicMock|AsyncMock|patch|Mock|call)\b', test_code
        ))
        if mock_names_used:
            # Check whether import already exists
            has_import = bool(re.search(
                r'from\s+unittest\.mock\s+import', test_code
            ))
            if has_import:
                # Extend existing import to include any missing names
                def _extend_import(m):
                    existing = {n.strip() for n in m.group(1).split(",")}
                    combined = sorted(existing | mock_names_used)
                    return f"from unittest.mock import {', '.join(combined)}"
                test_code = re.sub(
                    r'from\s+unittest\.mock\s+import\s+([\w,\s]+)',
                    _extend_import,
                    test_code,
                    count=1,
                )
            else:
                # Prepend import after the last 'import' line near the top
                import_line = (
                    f"from unittest.mock import {', '.join(sorted(mock_names_used))}\n"
                )
                # Insert after the last top-of-file import block
                insert_after = 0
                for i, line in enumerate(test_code.splitlines()):
                    if line.startswith(("import ", "from ")):
                        insert_after = i
                lines = test_code.splitlines(keepends=True)
                lines.insert(insert_after + 1, import_line)
                test_code = "".join(lines)

        return test_code

    @staticmethod
    def _load_trust_rules_for_category(category: str) -> str:
        """
        Read permanent structural rules for this plugin category from
        prompt_trust.json['categories'] and return an injected prompt string.

        Written by sandbox_runner._record_blocking_trust_rule() when the AST
        scanner catches a blocking subprocess call. Persists across restarts —
        the LLM sees the constraint on every future attempt for this category,
        including the first attempt after a system restart.

        Returns empty string if no rules exist (safe, non-fatal).
        """
        import json as _json
        # Try learning/ directory relative to the project root
        _candidates = [
            os.path.join("learning", "prompt_trust.json"),
            os.path.join(os.path.dirname(os.path.abspath(__file__)),
                         "..", "learning", "prompt_trust.json"),
        ]
        trust_file = next((p for p in _candidates if os.path.exists(p)), None)
        if not trust_file:
            return ""
        try:
            with open(trust_file, "r", encoding="utf-8") as f:
                data = _json.load(f)
            rules = data.get("categories", {}).get(category, {})
            lines = []
            if rules.get("always_flag_blocking_calls"):
                lines.append(
                    f"⚠️ PERMANENT CATEGORY CONSTRAINT for '{category}': "
                    "Synchronous blocking calls (subprocess.run, subprocess.call, "
                    "subprocess.Popen, os.system) are PERMANENTLY BANNED. "
                    "You MUST use asyncio.create_subprocess_shell with "
                    "asyncio.wait_for(timeout=30). This rule persists across sessions."
                )
            if rules.get("enforce_asyncio_subprocess"):
                lines.append(
                    f"⚠️ PERMANENT CATEGORY CONSTRAINT for '{category}': "
                    "All subprocess operations MUST use asyncio.create_subprocess_shell."
                )
            return ("\n".join(lines)) if lines else ""
        except Exception:
            return ""

    @staticmethod
    def _intent_to_plugin_name(intent: str) -> str:
        """Converts an intent string to a valid snake_case plugin name."""
        name = intent.lower().strip()
        name = re.sub(r"[^a-z0-9_]", "_", name)
        name = re.sub(r"_+", "_", name).strip("_")
        if not name:
            name = "auto_plugin"
        return f"{name}_plugin"

    @staticmethod
    def _parse_separator_response(raw: str) -> tuple[str, str]:
        """
        Parses the separator-based code generation response.

        Expected format:
            ===PLUGIN_CODE===
            <plugin.py content>
            ===TEST_CODE===
            <test_plugin.py content>
            ===END===

        Falls back gracefully if the LLM didn't follow the format exactly.
        """
        import re

        # Try exact separator format first
        plugin_match = re.search(
            r"===PLUGIN_CODE===\s*(.*?)\s*===TEST_CODE===",
            raw, re.DOTALL
        )
        test_match = re.search(
            r"===TEST_CODE===\s*(.*?)\s*===END===",
            raw, re.DOTALL
        )

        if plugin_match and test_match:
            plugin_code = plugin_match.group(1).strip()
            tests       = test_match.group(1).strip()
            # Strip any markdown fences the LLM added inside the sections
            plugin_code = PluginGenerator._strip_code_fences(plugin_code)
            tests       = PluginGenerator._strip_code_fences(tests)
            return plugin_code, tests

        # Fallback: try to extract two code blocks from the raw text
        blocks = re.findall(r"```(?:python)?\s*(.*?)\s*```", raw, re.DOTALL)
        if len(blocks) >= 2:
            return blocks[0].strip(), blocks[1].strip()
        if len(blocks) == 1:
            return blocks[0].strip(), ""

        # Last resort: treat the whole response as plugin code
        return raw.strip(), ""

    @staticmethod
    def _strip_code_fences(code: str) -> str:
        """
        Remove markdown code fences from LLM output and normalize whitespace.

        LLMs sometimes return plugin code with \n\n between every single line
        (each line gets its own JSON string line). This makes the code appear
        as an empty class body to the validator — methods look like they are
        missing because everything is separated by blank lines.
        We collapse 3+ consecutive newlines to 2 (PEP 8 max between defs).
        """
        import re
        if not code:
            return ""
        code = code.strip()
        if code.startswith("```python"):
            code = code.split("```python", 1)[1]
            if "```" in code:
                code = code.rsplit("```", 1)[0]
        elif code.startswith("```"):
            code = code.split("```", 1)[1]
            if "```" in code:
                code = code.rsplit("```", 1)[0]
        # Collapse excessive blank lines injected by the LLM
        code = re.sub(r"\n{3,}", "\n\n", code)
        return code.strip()


    async def generate(self, spec: dict) -> dict:
        """
        API-facing entry point called by POST /api/plugins/generate.

        Accepts a spec dict:
            name        – desired plugin/capability name
            description – natural language description of what it should do
            intent      – intent string (defaults to name if omitted)
            capabilities – list of capability tags
            parameters  – param dict for context

        Returns a result dict with files list and message.
        """
        name        = spec.get("name", "").strip()
        description = spec.get("description", f"Plugin for {name}")
        intent      = spec.get("intent") or name.replace("-", "_").lower()

        if not intent:
            return {"files": [], "message": "'name' or 'intent' is required."}

        plugin_name = self._intent_to_plugin_name(intent)
        plugin_dir  = os.path.join(PLUGINS_INSTALLED_DIR, plugin_name)

        self.logger.info(
            "🏭 API-triggered generation: intent='%s' plugin='%s'",
            intent, plugin_name,
        )

        # Use failure_summary with the description as context
        failure_context = {
            "description":          description,
            "consecutive_failures":  0,
            "common_reasons":        [f"User requested: {description}"],
        }

        success = await self.generate_plugin_for_intent(
            intent=intent,
            failure_summary=failure_context,
            reason=f"API request: {description}",
        )

        plugin_file = os.path.join(plugin_dir, "plugin.py")
        manifest_file = os.path.join(plugin_dir, "manifest.json")
        files = []
        if os.path.exists(plugin_file):
            files.append(plugin_file)
        if os.path.exists(manifest_file):
            files.append(manifest_file)

        return {
            "files":   files,
            "plugin":  plugin_name,
            "intent":  intent,
            "success": success,
            "message": (
                f"Plugin '{plugin_name}' generated and deployed."
                if success else
                f"Plugin generation failed after {MAX_GENERATION_ATTEMPTS} attempts."
            ),
        }


    async def _on_plugin_rejected(self, event):
        """
        User rejected a generated plugin.
        1. Blocks further auto-generation for this intent.
        2. Writes a persistent rejection rule to episodic memory — so the
           next generation attempt's failure_context includes the user's reason.
        3. Writes a negative signal to learner's override_counts — so the
           category's ranking is demoted for this intent.
        """
        data        = event.data or {}
        plugin_name = data.get("name", "")
        reason      = data.get("reason", "Rejected by user")
        intent      = data.get("intent", plugin_name.replace("_plugin", "").replace("_", " "))

        if not plugin_name:
            return

        self.logger.warning(
            "🚫 Plugin '%s' rejected by user: %s. Blocking re-generation.",
            plugin_name, reason,
        )

        # Mark as untrusted in registry
        try:
            from plugins.registry import plugin_registry
            from plugins.manifest_schema import PluginStatus
            plugin_registry.revoke_trust(plugin_name, reason)
        except Exception as exc:
            self.logger.debug("Could not revoke trust for '%s': %s", plugin_name, exc)

        # Block in gap detector so it won't re-trigger
        try:
            from plugins.capability_gap_detector import capability_gap_detector
            capability_gap_detector._blocked.add(plugin_name)
        except Exception as exc:
            self.logger.debug("Could not block intent '%s': %s", plugin_name, exc)

        # ── Persistent rejection learning ─────────────────────────────────────
        # Write a failure episode tagged with the user's reason so the next
        # generation attempt (if manually triggered) sees it in failure_context.
        try:
            await episodic_memory._record_failure(
                intent=intent,
                reason=f"[user_rejected] {reason[:300]}",
                attempts=1,
            )
            category = template_engine.get_category(intent) if intent else "generic"
            import time as _t
            await episodic_memory.store(
                key=f"plugin_rejected:{plugin_name}:{int(_t.time())}",
                content={
                    "intent":           intent,
                    "plugin_name":      plugin_name,
                    "stage":            "user_rejected",
                    "reason":           reason[:400],
                    "suggested_tweaks": (
                        f"The user rejected this plugin with reason: '{reason}'. "
                        f"Address this specific issue in the next attempt."
                    ),
                    "category":         category,
                    "outcome":          "failure",
                    "capability_used":  f"plugin_generation:{category}",
                },
                tags=["plugin_generation", "user_rejected", category, intent],
            )
        except Exception as exc:
            self.logger.debug("Could not record rejection episode: %s", exc)

        bus.publish(
            "plugin_blocked",
            {"name": plugin_name, "reason": reason},
            source="plugin_generator",
        )


# Global instance
plugin_generator = PluginGenerator()