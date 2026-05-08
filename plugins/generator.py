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
        """User has approved a pending plugin. Hot-reload it."""
        data        = event.data or {}
        plugin_name = data.get("name", "")
        plugin_dir  = data.get("plugin_dir", "")

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
                {"name": plugin_name, "intent": data.get("intent", "")},
                source="plugin_generator",
            )
            self.logger.info(f"🚀 Plugin '{plugin_name}' is now live.")

        except Exception as e:
            self.logger.error(f"Failed to deploy approved plugin '{plugin_name}': {e}")

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

        # ── Call 1: Generate code as plain text (no JSON mode) ────────────────
        # Embedding code in JSON causes Groq to escape newlines and truncate
        # method bodies, making the validator think no methods are implemented.
        code_prompt = f"""You are an expert Python developer for a self-evolving AI OS agent.
Generate a complete, production-quality plugin to handle the failing intent.

FAILING INTENT: "{intent}"
PLUGIN CATEGORY: {failure_context.get('category', category if 'category' in dir() else 'generic')}
CONSECUTIVE FAILURES: {failure_context.get('consecutive_failures', 0)}
COMMON FAILURE REASONS: {common_reasons}
PREVIOUS ATTEMPT FAILURES: {prev_attempts}

PATTERN LIBRARY — working code patterns for common tasks, copy what you need:
{template_engine.get_pattern_library()}

PLUGIN SKELETON (fill in the TODO sections with real, working logic):
```python
{skeleton}
```

TEST SKELETON (fill in with real test cases that match your implementation):
```python
{test_skeleton}
```

CRITICAL RULES:
1. The plugin class MUST subclass BasePlugin
2. Do NOT add sys.path manipulation or from __future__ imports — injected automatically.
3. ALWAYS import asyncio at the top if you use await anywhere
4. run() MUST return a dict with a "status" key ("success" or "error")
5. Access ALL services via: capability_registry.get("service_name")
6. NEVER import from automation/, context/, core/, or safety/ directly
7. ALL exceptions must be caught and returned as {{"status": "error", "message": str(e)}}
8. Tests must be runnable standalone with pytest (no external services needed)
9. Use time.sleep() inside threads, asyncio.sleep() inside async functions

Output EXACTLY this format — no other text before or after:

===PLUGIN_CODE===
<complete plugin.py source code here>
===TEST_CODE===
<complete test_plugin.py source code here>
===END===
"""
        # ── Call 2: Metadata only (small JSON, no code) ───────────────────────
        meta_prompt = f"""Return ONLY valid JSON metadata for a plugin named '{plugin_name}'.
No markdown, no explanation, just the JSON object.

{{
    "name": "{plugin_name}",
    "description": "<one sentence: what this plugin does for intent '{intent}'>",
    "permissions": ["ui_interaction"],
    "risk_level": "low",
    "capabilities": ["{intent}"]
}}
"""
        try:
            # Call 1: plain text code generation
            raw_text = await llm_client.generate(code_prompt, use_json=False)

            plugin_code = ""
            tests = ""

            if raw_text and isinstance(raw_text, str):
                plugin_code, tests = self._parse_separator_response(raw_text)
            elif isinstance(raw_text, dict):
                # Fallback: LLM still returned JSON despite use_json=False
                plugin_code = self._strip_code_fences(raw_text.get("plugin_code", ""))
                tests       = self._strip_code_fences(raw_text.get("tests", ""))

            # Call 2: metadata JSON
            metadata = {}
            try:
                meta_result = await llm_client.generate(meta_prompt, use_json=True)
                if isinstance(meta_result, dict) and "name" in meta_result:
                    metadata = meta_result
            except Exception as me:
                self.logger.debug(f"Metadata generation failed (non-fatal): {me}")
                metadata = {
                    "name": plugin_name,
                    "description": f"Auto-generated plugin for: {intent}",
                    "permissions": [],
                    "risk_level": "low",
                    "capabilities": [intent],
                }

            if not metadata:
                metadata = {
                    "name": plugin_name,
                    "description": f"Auto-generated plugin for: {intent}",
                    "permissions": [],
                    "risk_level": "low",
                    "capabilities": [intent],
                }

            snippet = (plugin_code or "")[:200].replace("\n", "↵")
            self.logger.debug(f"Generated code snippet: {snippet}")

            return plugin_code, tests, metadata

        except Exception as e:
            self.logger.error(f"LLM generation failed: {e}")

        return "", "", {}

    # ── Post-Generation ────────────────────────────────────────────────────────

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
        manifest = PluginManifest(
            name=plugin_name,
            description=metadata.get("description", f"Plugin for {intent}"),
            intent=intent,
            version="1.0",
            # capabilities exposed by this plugin — used by loader.list_plugins()
            # and the dashboard to show what each plugin can do
            capabilities=metadata.get("capabilities", [intent]),
            permissions=metadata.get("permissions", []),
            risk_level=RiskLevel(metadata.get("risk_level", "medium")),
            status=PluginStatus.PENDING,
            trusted=False,
            requires_confirmation=True,
            tags=[intent, "auto-generated"],
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
        """User rejected a generated plugin. Block further generation for this intent."""
        data        = event.data or {}
        plugin_name = data.get("name", "")
        reason      = data.get("reason", "Rejected by user")

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

        bus.publish(
            "plugin_blocked",
            {"name": plugin_name, "reason": reason},
            source="plugin_generator",
        )


# Global instance
plugin_generator = PluginGenerator()