"""
plugins/plugin_validator.py

LLM-based quality gate for generated plugin code.
Mirrors the pattern of debugging/fix_validator.py but scoped for plugins:
  - Audits generated code for correctness, safety, BasePlugin compliance
  - Checks that plugins only access services via the registry (not direct imports)
  - Returns structured validation result with suggested tweaks
  - Uses Gemini (critique role) via llm_client.critique()
"""
from __future__ import annotations

import logging

from brain.llm_client import llm_client

logger = logging.getLogger("PluginValidator")


class PluginValidator:
    """
    🛡️ Quality gate for LLM-generated plugin code.

    Uses Gemini to audit the generated plugin before sandbox testing.
    Returns: {"valid": bool, "reason": str, "suggested_tweaks": str}
    """

    def __init__(self):
        self.logger = logging.getLogger("PluginValidator")

    async def validate_plugin(
        self,
        plugin_name: str,
        plugin_code: str,
        intent: str,
        failure_summary: dict | None = None,
    ) -> dict:
        """
        Sends generated plugin code to Gemini for critique.

        Args:
            plugin_name:      Name of the plugin being validated
            plugin_code:      Full Python source code string
            intent:           The capability gap intent this plugin fills
            failure_summary:  Optional dict from episodic_memory with failure context

        Returns:
            {
                "valid": True/False,
                "reason": "...",
                "suggested_tweaks": "...",
                "safety_concerns": "..."
            }
        """
        self.logger.info(f"🔍 Validating plugin '{plugin_name}' for intent '{intent}'...")

        failure_context = ""
        if failure_summary:
            failure_context = f"""
FAILURE CONTEXT (why this plugin was generated):
- Intent that kept failing: {failure_summary.get('intent')}
- Consecutive failures: {failure_summary.get('consecutive_failures')}
- Common failure reasons: {failure_summary.get('common_reasons')}
"""

        prompt = f"""
You are the strict code auditor for a self-evolving AI OS agent.
A code generator has produced a new plugin to fill a capability gap.
You must rigorously review it before it goes live.

PLUGIN NAME: {plugin_name}
INTENT IT FILLS: {intent}
{failure_context}

GENERATED PLUGIN CODE:
```python
{plugin_code}
```

AUDIT CHECKLIST — verify ALL of these:

1. INTERFACE COMPLIANCE
   - Does the class subclass BasePlugin?
   - Does it implement async run(self, context, args) -> dict?
   - Does run() return a dict with at minimum a "status" key?
   - Does it implement validate(self, args) -> str | None?

2. SAFETY CONSTRAINTS
   - Does it avoid direct imports of automation/, context/, core/, safety/?
   - Does it access automation/UI services ONLY via the service registry?
     (e.g., capability_registry.get("vision_service") NOT from automation.vision_model import ...)
   - Does it check if the service returned is None before using it?
     (e.g., if service is None: return {{"status": "error", ...}})
     NOTE: The registry returns the service object directly or None — there is NO
     is_available() method. A None-check is the ONLY correct availability check.
   - Does it avoid os.system(), subprocess, eval(), exec()?

3. ERROR HANDLING
   - Does run() handle exceptions and return {{"status": "error", "message": "..."}} ?
   - Are all external calls wrapped in try/except?

4. LOGIC CORRECTNESS
   - Does the plugin logic actually address the failing intent: "{intent}"?
   - Is the logic sound and non-trivial (not just returning hardcoded values)?

5. ACCEPTABLE PATTERNS (do NOT reject for these)
   - Using `threading.Thread` with `daemon=True` for background tasks is CORRECT
     and safe — do not reject plugins for using threads.
   - Using `time.sleep()` inside a thread is CORRECT for rate-limiting loops.
   - Using `asyncio.sleep()` inside an async function is CORRECT.
   - Using `keyboard`, `pyautogui`, `pynput` libraries for UI automation is CORRECT
     for automation-category plugins — do not reject for using these libraries.
   - A None-check on registry.get() is the COMPLETE and CORRECT availability check.
     Do NOT require is_available() — that method does not exist in this codebase.
   - Do NOT invent requirements not listed in this checklist.
   - Do NOT reject a plugin for style preferences or minor improvements.
     Only reject if a checklist item is genuinely violated.

CRITICAL INSTRUCTION:
Return STRICTLY valid JSON. No markdown. No text outside the JSON.

{{
    "valid": true or false,
    "reason": "Detailed explanation of pass/fail covering all checklist items",
    "suggested_tweaks": "If failed: specific code changes needed. If passed: empty string.",
    "safety_concerns": "List any safety issues found, or empty string if none."
}}
"""
        try:
            result = await llm_client.critique(prompt, use_json=True)

            # Normalize: ensure all expected keys exist
            if isinstance(result, dict):
                return {
                    "valid": bool(result.get("valid", False)),
                    "reason": result.get("reason", "No reason provided"),
                    "suggested_tweaks": result.get("suggested_tweaks", ""),
                    "safety_concerns": result.get("safety_concerns", ""),
                }

            # If result came back as raw (shouldn't happen with use_json=True)
            self.logger.warning("Validator returned non-dict result, defaulting to invalid.")
            return {
                "valid": False,
                "reason": "Validator returned malformed response.",
                "suggested_tweaks": "Re-run generation.",
                "safety_concerns": "",
            }

        except Exception as e:
            self.logger.error(f"Plugin validation failed with exception: {e}")
            return {
                "valid": False,
                "reason": f"Validator exception: {e}",
                "suggested_tweaks": "Check system logs.",
                "safety_concerns": "",
            }

    async def validate_tests(
        self, plugin_name: str, test_code: str, plugin_code: str
    ) -> dict:
        """
        Secondary audit: validates the auto-generated test file.
        Ensures tests actually exercise the plugin and check its outputs.
        """
        self.logger.info(f"🔍 Validating test suite for plugin '{plugin_name}'...")

        prompt = f"""
You are auditing an auto-generated test file for a plugin in a self-evolving AI OS.

PLUGIN CODE:
```python
{plugin_code[:2000]}
```

GENERATED TEST CODE:
```python
{test_code}
```

Verify:
1. Tests import and instantiate the plugin correctly
2. Tests call run() with realistic args
3. Tests assert on the "status" key in the result
4. Tests cover at least one success case and one failure/error case
5. Tests are runnable by pytest without external dependencies

Return STRICTLY valid JSON:
{{
    "valid": true or false,
    "reason": "...",
    "suggested_tweaks": "..."
}}
"""
        try:
            result = await llm_client.critique(prompt, use_json=True)
            if isinstance(result, dict):
                return {
                    "valid": bool(result.get("valid", False)),
                    "reason": result.get("reason", ""),
                    "suggested_tweaks": result.get("suggested_tweaks", ""),
                }
        except Exception as e:
            self.logger.error(f"Test validation failed: {e}")

        return {
            "valid": False,
            "reason": f"Test validator exception",
            "suggested_tweaks": "Regenerate tests.",
        }


# Global instance
plugin_validator = PluginValidator()