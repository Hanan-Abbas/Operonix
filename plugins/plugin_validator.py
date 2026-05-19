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
        category: str = "generic",
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

        # ── Structural pre-check ───────────────────────────────────────────────
        # Groq's JSON mode sometimes truncates method bodies to keep JSON valid,
        # causing the LLM auditor to incorrectly report "no methods implemented"
        # even when the code is fine. We do a fast regex pre-check first:
        # if all required structural elements are present in the raw code string,
        # we know the interface is implemented and skip the LLM for that check.
        precheck = self._structural_precheck(plugin_code, plugin_name, intent, category)
        if precheck is not None:
            return precheck
        # ──────────────────────────────────────────────────────────────────────

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


    # ── Structural pre-check ──────────────────────────────────────────────────

    @staticmethod
    def _normalize_plugin_code(code: str) -> str:
        """
        Collapse excessive blank lines that Groq JSON mode injects.
        When plugin_code is embedded in a JSON string, the LLM sometimes
        puts \n\n between every line making method bodies appear empty.
        """
        import re
        code = re.sub(r"\n{3,}", "\n\n", code)
        return code.strip()

    def _structural_precheck(
        self, plugin_code: str, plugin_name: str, intent: str,
        category: str = "generic",
    ) -> dict | None:
        """
        Fast structural check that bypasses the LLM auditor for the common
        false-negative case where Groq JSON mode truncates method bodies.

        Logic:
        - Normalize whitespace first (collapse triple+ newlines)
        - Run AST semantic checks (forbidden imports, _execute isolation,
          blocking calls, stdout cleanliness) — these are hard failures that
          return immediately with precise error messages, no LLM needed.
        - Check all required structural tokens are present
        - If ALL present → pass immediately (valid=True)
        - If NONE present → the code is genuinely empty → fail immediately
        - If SOME present → ambiguous → let the LLM decide (return None)

        Returns:
            dict  → definitive pass or fail result (skip LLM)
            None  → ambiguous, let LLM audit proceed
        """
        import re
        import ast as _ast

        normalized = self._normalize_plugin_code(plugin_code)

        # ── AST Semantic Checks (hard failures, precise messages) ─────────────
        # These run before the structural token checks because they give
        # the generator exact, actionable feedback for the retry prompt.
        # Failures here skip the LLM auditor entirely — no token cost.

        semantic_fail = self._ast_semantic_check(normalized, plugin_name, category)
        if semantic_fail is not None:
            return semantic_fail

        # Background plugins run their logic in daemon threads — the run()
        # method itself just starts threads and returns. The try/except lives
        # inside the thread worker, not in run() directly. Relax that check.
        requires_try_except = category not in ("background",)

        checks = {
            "subclasses_baseplugin": bool(re.search(
                r"class\s+\w+\s*\(\s*BasePlugin\s*\)", normalized
            )),
            "has_async_run": bool(re.search(
                r"async\s+def\s+run\s*\(", normalized
            )),
            "has_validate": bool(re.search(
                r"def\s+validate\s*\(", normalized
            )),
            "run_returns_dict": bool(re.search(
                r'return\s+\{[^}]*["\']status["\']', normalized
            )),
            "has_try_except": (
                ("try:" in normalized and "except" in normalized)
                if requires_try_except else True
            ),
        }

        passed = sum(checks.values())
        total  = len(checks)

        self.logger.debug(
            f"Structural pre-check for '{plugin_name}': "
            + ", ".join(f"{k}={'✅' if v else '❌'}" for k, v in checks.items())
        )

        if passed == total:
            # All checks pass — definitively valid, skip LLM audit
            self.logger.info(
                f"✅ Structural pre-check PASSED for '{plugin_name}' "
                f"({passed}/{total}) — skipping LLM audit."
            )
            return {
                "valid": True,
                "reason": (
                    f"Structural pre-check passed all {total} checks: "
                    "BasePlugin subclass, async run(), validate(), "
                    "status return, and try/except all present."
                ),
                "suggested_tweaks": "",
                "safety_concerns": "",
            }

        if passed == 0:
            # Nothing present — code is genuinely empty/broken
            self.logger.warning(
                f"❌ Structural pre-check FAILED for '{plugin_name}' "
                f"(0/{total}) — code appears empty or malformed."
            )
            return {
                "valid": False,
                "reason": (
                    "Structural pre-check failed: the plugin code appears to be "
                    "empty or malformed — no BasePlugin subclass, async run(), "
                    "validate(), or return dict found."
                ),
                "suggested_tweaks": (
                    "Implement class MyPlugin(BasePlugin) with async run(self, "
                    "context, args) -> dict and def validate(self, args) -> str | None."
                ),
                "safety_concerns": "",
            }

        # Partial pass — ambiguous, let LLM decide
        self.logger.debug(
            f"Structural pre-check partial ({passed}/{total}) for "
            f"'{plugin_name}' — deferring to LLM audit."
        )
        return None

    # ── AST Semantic Checker ──────────────────────────────────────────────────

    @staticmethod
    def _ast_semantic_check(
        plugin_code: str, plugin_name: str, category: str
    ) -> dict | None:
        """
        Parse the plugin with the AST module and enforce hard structural rules
        that regex can't reliably detect.

        Checks (each returns a precise error dict on failure, None if OK):

        1. FORBIDDEN IMPORTS — direct imports from automation/, context/,
           core/, safety/. Plugins must use capability_registry.get() instead.

        2. BLOCKING SUBPROCESS CALLS — subprocess.run / subprocess.call /
           subprocess.Popen / os.system. These block the async event loop.
           Plugins must use asyncio.create_subprocess_shell.

        3. _execute ISOLATION — run() must call self._execute() (or self.run
           must contain the contract guard). If the plugin has an _execute()
           method, validate() must be called before it inside run(). This
           ensures the locked contract from template_engine is preserved.

        4. STDOUT CLEANLINESS — bare print() calls whose output would appear
           before the JSON return value corrupt the sandbox JSON validation.
           Allowed: print() inside non-run methods (e.g. __init__, helpers).
           Flagged: print() at top level of run() or _execute() bodies.

        Returns a "valid=False" dict on the first violation found, else None.
        """
        import ast as _ast
        import re as _re

        # ── Parse ─────────────────────────────────────────────────────────────
        try:
            tree = _ast.parse(plugin_code)
        except SyntaxError as e:
            return {
                "valid": False,
                "reason": f"SyntaxError in generated code: {e}",
                "suggested_tweaks": "Fix the syntax error reported above.",
                "safety_concerns": "",
            }

        # ── Check 1: Forbidden imports ────────────────────────────────────────
        _FORBIDDEN_PREFIXES = ("automation.", "context.", "core.", "safety.")
        for node in _ast.walk(tree):
            if isinstance(node, (_ast.Import, _ast.ImportFrom)):
                module = ""
                if isinstance(node, _ast.ImportFrom):
                    module = node.module or ""
                elif isinstance(node, _ast.Import):
                    for alias in node.names:
                        module = alias.name or ""
                        if any(module == p.rstrip(".") or module.startswith(p)
                               for p in _FORBIDDEN_PREFIXES):
                            return {
                                "valid": False,
                                "reason": (
                                    f"Forbidden import: 'import {module}'. "
                                    f"Plugins must not import directly from "
                                    f"automation/, context/, core/, or safety/. "
                                    f"Use capability_registry.get('service_name') instead."
                                ),
                                "suggested_tweaks": (
                                    f"Remove 'import {module}' and replace with:\n"
                                    f"    svc = capability_registry.get('service_name')\n"
                                    f"    if svc is None:\n"
                                    f"        return {{\"status\": \"error\", \"message\": \"service unavailable\"}}"
                                ),
                                "safety_concerns": f"Direct internal import: {module}",
                            }
                if any(module == p.rstrip(".") or module.startswith(p)
                       for p in _FORBIDDEN_PREFIXES):
                    return {
                        "valid": False,
                        "reason": (
                            f"Forbidden import: 'from {module} import ...'. "
                            f"Plugins must not import from automation/, context/, "
                            f"core/, or safety/. Use capability_registry.get() instead."
                        ),
                        "suggested_tweaks": (
                            f"Remove 'from {module} import ...' and use the registry:\n"
                            f"    svc = capability_registry.get('service_name')\n"
                            f"    if svc is None:\n"
                            f"        return {{\"status\": \"error\", \"message\": \"service unavailable\"}}"
                        ),
                        "safety_concerns": f"Direct internal import: {module}",
                    }

        # ── Check 2: Blocking subprocess calls ───────────────────────────────
        # We already scan for these in generator.py's static scanner, but
        # plugin_validator is the last gate before sandbox — double-check here
        # for plugins that arrive via plugin_evolver or manual edits.
        _BLOCKING = {
            ("subprocess", "run"), ("subprocess", "call"),
            ("subprocess", "check_output"), ("subprocess", "Popen"),
        }
        for node in _ast.walk(tree):
            if isinstance(node, _ast.Call):
                # subprocess.run(...) → Attribute(value=Name('subprocess'), attr='run')
                if (
                    isinstance(node.func, _ast.Attribute)
                    and isinstance(node.func.value, _ast.Name)
                    and (node.func.value.id, node.func.attr) in _BLOCKING
                ):
                    call_str = f"{node.func.value.id}.{node.func.attr}"
                    return {
                        "valid": False,
                        "reason": (
                            f"Blocking subprocess call detected: {call_str}(). "
                            f"This blocks the entire async agent event loop. "
                            f"Use asyncio.create_subprocess_shell with asyncio.wait_for instead."
                        ),
                        "suggested_tweaks": (
                            f"Replace {call_str}() with:\n"
                            "    proc = await asyncio.create_subprocess_shell(\n"
                            "        cmd, stdout=asyncio.subprocess.PIPE,\n"
                            "        stderr=asyncio.subprocess.PIPE)\n"
                            "    stdout_b, stderr_b = await asyncio.wait_for(\n"
                            "        proc.communicate(), timeout=30)"
                        ),
                        "safety_concerns": f"Blocking call: {call_str}()",
                    }
                # os.system(...) → Attribute(value=Name('os'), attr='system')
                if (
                    isinstance(node.func, _ast.Attribute)
                    and isinstance(node.func.value, _ast.Name)
                    and node.func.value.id == "os"
                    and node.func.attr == "system"
                ):
                    return {
                        "valid": False,
                        "reason": (
                            "Blocking call detected: os.system(). "
                            "This blocks the entire async event loop. "
                            "Use asyncio.create_subprocess_shell with asyncio.wait_for instead."
                        ),
                        "suggested_tweaks": (
                            "Replace os.system() with asyncio.create_subprocess_shell."
                        ),
                        "safety_concerns": "Blocking call: os.system()",
                    }

        # ── Check 3: _execute isolation — validate() called in run() ─────────
        # Only enforced if the plugin defines _execute() — if it doesn't, it's
        # using the old inline pattern and the structural token checks handle it.
        has_execute_method = any(
            isinstance(node, _ast.AsyncFunctionDef) and node.name == "_execute"
            for node in _ast.walk(tree)
        )
        if has_execute_method:
            # Find the run() method and check it calls self.validate()
            run_calls_validate = False
            for node in _ast.walk(tree):
                if (
                    isinstance(node, _ast.AsyncFunctionDef)
                    and node.name == "run"
                ):
                    for child in _ast.walk(node):
                        if (
                            isinstance(child, _ast.Call)
                            and isinstance(child.func, _ast.Attribute)
                            and child.func.attr == "validate"
                        ):
                            run_calls_validate = True
                            break
            if not run_calls_validate:
                return {
                    "valid": False,
                    "reason": (
                        "run() defines _execute() but does not call self.validate() "
                        "before invoking it. The validate guard in run() is mandatory — "
                        "it prevents unsafe args from reaching _execute()."
                    ),
                    "suggested_tweaks": (
                        "Add at the start of run():\n"
                        "    error = self.validate(args)\n"
                        "    if error:\n"
                        "        return {\"status\": \"error\", \"message\": error}"
                    ),
                    "safety_concerns": "validate() bypassed — unsafe args reach _execute()",
                }

        # ── Check 4: Stdout cleanliness (bare print in run/_execute) ─────────
        # A print() in run() or _execute() outputs text before the JSON dict,
        # breaking sandbox JSON extraction. Allowed in helper methods.
        _CHECKED_METHODS = {"run", "_execute"}
        for node in _ast.walk(tree):
            if (
                isinstance(node, (_ast.FunctionDef, _ast.AsyncFunctionDef))
                and node.name in _CHECKED_METHODS
            ):
                for child in _ast.walk(node):
                    if (
                        isinstance(child, _ast.Call)
                        and isinstance(child.func, _ast.Name)
                        and child.func.id == "print"
                    ):
                        return {
                            "valid": False,
                            "reason": (
                                f"print() call detected inside {node.name}(). "
                                "The sandbox captures stdout as the plugin's JSON output — "
                                "any text printed before the return dict breaks JSON parsing. "
                                "Use self.logger.debug() which writes to the log file, not stdout."
                            ),
                            "suggested_tweaks": (
                                f"Remove all print() calls from {node.name}(). "
                                "Replace with self.logger.debug('...') if you need debug output."
                            ),
                            "safety_concerns": "",
                        }

        # All AST checks passed
        return None


# Global instance
plugin_validator = PluginValidator()