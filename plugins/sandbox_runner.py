"""
plugins/sandbox_runner.py

Orchestrates the full pre-deployment test cycle for a new plugin:

1. LLM validation (plugin_validator) — code audit before any execution
2. Sandbox execution test (safety.sandbox) — isolated subprocess run
3. Pytest test suite (safety.sandbox.run_test_suite) — automated tests

Returns a structured report used by generator.py to decide whether to
deploy the plugin or retry.
"""
from __future__ import annotations

import logging
import os

from core.event_bus import bus
from plugins.plugin_validator import plugin_validator
from safety.sandbox import sandbox

logger = logging.getLogger("SandboxRunner")


class SandboxRunner:
    """
    Full pre-deployment validation pipeline for generated plugins.

    Used by:
        generator.py — before writing the plugin to installed/
        plugin_evolver.py — before deploying an evolved version
    """

    def __init__(self):
        self.logger = logging.getLogger("SandboxRunner")

    async def run_full_pipeline(
        self,
        plugin_name: str,
        plugin_code: str,
        test_code: str,
        plugin_dir: str,
        intent: str,
        failure_summary: dict | None = None,
        category: str = "generic",
    ) -> dict:
        """
        Runs the complete validation pipeline.

        Args:
            category: Plugin category from template_engine (background, automation,
                      web, file, command, system, data, generic). Passed to the
                      sandbox so it can apply the right execution strategy, and to
                      the validator so it applies category-aware audit rules.

        Returns:
        {
            "passed": bool,
            "stage_failed": "llm_audit" | "sandbox_run" | "pytest" | None,
            "llm_audit": {...},
            "sandbox_run": {...},
            "pytest": {...},
            "ready_for_approval": bool,
        }
        """
        report = {
            "passed": False,
            "stage_failed": None,
            "llm_audit": {},
            "sandbox_run": {},
            "pytest": {},
            "ready_for_approval": False,
        }

        # ── Stage 1: LLM Code Audit ───────────────────────────────────────────
        self.logger.info(f"🔍 [Stage 1] LLM audit for '{plugin_name}'...")
        audit = await plugin_validator.validate_plugin(
            plugin_name=plugin_name,
            plugin_code=plugin_code,
            intent=intent,
            failure_summary=failure_summary,
            category=category,
        )
        report["llm_audit"] = audit

        if not audit.get("valid", False):
            self.logger.warning(
                f"❌ [Stage 1] LLM audit rejected '{plugin_name}': {audit.get('reason')}"
            )
            report["stage_failed"] = "llm_audit"
            # Clean up the empty plugin dir created by generator.py before
            # the pipeline started — at Stage 1 no files have been written yet
            # so plugin_dir contains only the bare directory. Without cleanup
            # it persists across retries and triggers loader warnings on restart.
            self._cleanup_failed_plugin_dir(plugin_dir, plugin_name, "llm_audit")
            bus.publish(
                "plugin_validation_failed",
                {
                    "name": plugin_name,
                    "stage": "llm_audit",
                    "reason": audit.get("reason"),
                    "tweaks": audit.get("suggested_tweaks"),
                },
                source="sandbox_runner",
            )
            return report

        self.logger.info(f"✅ [Stage 1] LLM audit passed for '{plugin_name}'.")

        # Write plugin files to disk for sandbox execution
        plugin_file = os.path.join(plugin_dir, "plugin.py")
        test_dir    = os.path.join(plugin_dir, "tests")
        test_file   = os.path.join(test_dir, "test_plugin.py")

        os.makedirs(test_dir, exist_ok=True)

        # Fix double-brace escape sequences in generated test code.
        # The LLM sometimes copies the template skeleton verbatim, including
        # {{ }} which are .format() escapes meaning literal { }.
        # In real Python code {{ is parsed as a set-containing-a-dict which
        # raises TypeError at runtime. Strip them before writing.
        test_code = self._fix_test_braces(test_code)

        # Patch sys.path bootstrap into the plugin before writing.
        # The sandbox subprocess has an isolated environment — the project
        # root is not on sys.path by default, causing 'No module named plugins'.
        # We inject a sys.path fix at the top of every generated plugin.
        plugin_code = self._patch_plugin_sys_path(plugin_code, plugin_dir)

        with open(plugin_file, "w") as f:
            f.write(plugin_code)
        with open(test_file, "w") as f:
            f.write(test_code)

        # ── Stage 2: Sandbox Execution Test ───────────────────────────────────
        self.logger.info(f"🏗️ [Stage 2] Sandbox execution test for '{plugin_name}'...")
        sandbox_result = await sandbox.run_plugin(
            plugin_path=plugin_file,
            context={"active_window": "test", "app_type": "sandbox_test"},
            args={},
            timeout=30,
            category=category,
        )
        report["sandbox_run"] = sandbox_result.to_dict()

        # ── stdout JSON extraction guard ───────────────────────────────────────
        # If the plugin printed debug text before its JSON (e.g. "Emitting
        # summary event\n{…}"), sandbox.py's json.loads() fails on the whole
        # buffer.  We detect that pattern here and patch the error to give the
        # LLM retry prompt precise, actionable feedback instead of a raw parse
        # exception.
        # NOTE: The definitive one-line fix lives in safety/sandbox.py —
        # wherever it calls  json.loads(stdout),  replace with:
        #   json.loads(SandboxRunner._extract_last_json(stdout))
        # That single change makes the whole system tolerant of stray print()s.
        raw_error = sandbox_result.error or ""
        if not sandbox_result.success and (
            "not valid JSON" in raw_error or "json" in raw_error.lower()
        ):
            raw_stdout = getattr(sandbox_result, "stdout", None) or raw_error
            extracted = SandboxRunner._extract_last_json(raw_stdout)
            if extracted != raw_stdout:
                self.logger.info(
                    "[Stage 2] Extracted clean JSON from noisy stdout for '%s'. "
                    "Plugin emitted debug text before JSON — patching error message "
                    "to give LLM precise feedback.", plugin_name
                )
                sandbox_result.error = (
                    "Plugin printed debug text to stdout before the JSON return value. "
                    "Remove ALL print() and logging.info() calls whose output appears "
                    "before the final return dict. The sandbox captures stdout as the "
                    "plugin output — only the JSON dict may be emitted there. "
                    "Silence all other output (use self.logger.debug() which writes "
                    "to the log file, not stdout)."
                )

        # ── Unwrap double-envelope if plugin AND sandbox_runner both wrapped ──
        # Pattern: {"status":"success","result":{"status":"success","result":{…}}}
        # We keep only the innermost payload.
        raw_dict = report.get("sandbox_run") or {}
        if (
            isinstance(raw_dict, dict)
            and raw_dict.get("status") == "success"
            and isinstance(raw_dict.get("result"), dict)
            and raw_dict["result"].get("status") == "success"
            and "result" in raw_dict["result"]
        ):
            report["sandbox_run"] = raw_dict["result"]

        if not sandbox_result.success:
            if sandbox_result.timed_out:
                self.logger.error(
                    f"⏱️ [Stage 2] Sandbox timeout for '{plugin_name}'."
                )
            else:
                self.logger.warning(
                    f"❌ [Stage 2] Sandbox execution failed for '{plugin_name}': "
                    f"{sandbox_result.error}"
                )
            report["stage_failed"] = "sandbox_run"
            self._cleanup_failed_plugin_dir(plugin_dir, plugin_name, "sandbox_run")
            bus.publish(
                "plugin_validation_failed",
                {
                    "name": plugin_name,
                    "stage": "sandbox_run",
                    "reason": sandbox_result.error,
                    "timed_out": sandbox_result.timed_out,
                },
                source="sandbox_runner",
            )
            return report

        self.logger.info(
            f"✅ [Stage 2] Sandbox execution passed "
            f"({sandbox_result.elapsed_ms}ms) for '{plugin_name}'."
        )

        # ── Stage 3: Pytest Test Suite ────────────────────────────────────────
        self.logger.info(f"🧪 [Stage 3] Running pytest for '{plugin_name}'...")
        test_result = await sandbox.run_test_suite(
            plugin_path=plugin_file,
            test_path=test_file,
            timeout=60,
        )
        report["pytest"] = test_result

        if not test_result.get("passed", False):
            self.logger.warning(
                f"❌ [Stage 3] Pytest failed for '{plugin_name}':\n"
                f"{test_result.get('output', '')[:500]}"
            )
            report["stage_failed"] = "pytest"
            self._cleanup_failed_plugin_dir(plugin_dir, plugin_name, "pytest")
            bus.publish(
                "plugin_validation_failed",
                {
                    "name": plugin_name,
                    "stage": "pytest",
                    "reason": test_result.get("output", "")[:300],
                },
                source="sandbox_runner",
            )
            return report

        self.logger.info(f"✅ [Stage 3] All tests passed for '{plugin_name}'.")

        # ── All stages passed ─────────────────────────────────────────────────
        report["passed"] = True
        report["ready_for_approval"] = True

        bus.publish(
            "plugin_ready_for_approval",
            {
                "name": plugin_name,
                "intent": intent,
                "plugin_dir": plugin_dir,
                "sandbox_elapsed_ms": sandbox_result.elapsed_ms,
            },
            source="sandbox_runner",
        )
        self.logger.info(
            f"🎉 Plugin '{plugin_name}' passed all validation stages. "
            f"Awaiting user approval."
        )
        return report

    async def quick_sandbox_test(
        self,
        plugin_path: str,
        context: dict | None = None,
        args: dict | None = None,
    ) -> dict:
        """
        Lightweight sandbox test without LLM audit or pytest.
        Used by plugin_evolver for quick iteration checks.
        """
        result = await sandbox.run_plugin(
            plugin_path=plugin_path,
            context=context or {},
            args=args or {},
            timeout=20,
        )
        return result.to_dict()


    def _cleanup_failed_plugin_dir(
        self, plugin_dir: str, plugin_name: str, stage: str
    ) -> None:
        """
        Removes an incomplete plugin directory left by a failed pipeline run.

        Called after Stage 2 or Stage 3 failures — at those points the
        directory exists (it was created by generator.py before the pipeline
        started) but contains no manifest.json yet (that is only written after
        ALL stages pass).  Leaving it causes the loader to warn
        "Missing manifest.json" on every restart.

        The generator retries with a fresh directory on the next attempt,
        so deleting here is always safe.
        """
        import shutil
        try:
            if os.path.isdir(plugin_dir):
                shutil.rmtree(plugin_dir, ignore_errors=True)
                self.logger.debug(
                    f"🧹 Cleaned up incomplete plugin dir after {stage} failure: "
                    f"{plugin_dir}"
                )
        except Exception as e:
            self.logger.debug(
                f"Could not clean up plugin dir '{plugin_dir}': {e}"
            )

    @staticmethod
    def _fix_test_braces(test_code: str) -> str:
        """
        Normalise {{ }} escape sequences in LLM-generated test code.

        The test skeleton template uses {{ and }} as .format() escapes so that
        literal braces survive the .format() call in get_test_skeleton().
        When the LLM copies the skeleton verbatim into its output, those
        escape sequences appear in the final test file as-is.

        In real Python (non-f-string) code:
          {{"key": "value"}}  →  BUILD_CONST_KEY_MAP + BUILD_SET
                                  → TypeError at runtime (dict is unhashable)

        We replace {{ → { and }} → } throughout the test source.
        We skip lines that are inside triple-quoted docstrings to avoid
        corrupting string literals that legitimately use braces.
        """
        import re as _re

        if not test_code or ("{{" not in test_code and "}}" not in test_code):
            return test_code

        # Simple line-by-line pass — skip docstring content
        lines = test_code.splitlines(keepends=True)
        result = []
        in_docstring = False
        docstring_char = ""

        for line in lines:
            stripped = line.strip()

            # Track docstring boundaries (triple-quote toggle)
            for quote in ('"""', "'''"):
                count = stripped.count(quote)
                if count % 2 == 1:          # odd occurrences → toggle
                    if not in_docstring:
                        in_docstring = True
                        docstring_char = quote
                    elif docstring_char == quote:
                        in_docstring = False

            if not in_docstring:
                line = line.replace("{{", "{").replace("}}", "}")

            result.append(line)

        return "".join(result)

    @staticmethod
    def _patch_plugin_sys_path(plugin_code: str, plugin_dir: str) -> str:
        """
        Rewrites the top of the plugin file to guarantee the final written
        file always begins with:

            from __future__ import annotations   ← line 1 (Python requirement)
            import sys as _sys, os as _os        ← sys.path bootstrap
            ...bootstrap...
            <module docstring if any>
            <rest of plugin body>

        Python mandates that `from __future__ import annotations` is the very
        first non-comment, non-whitespace statement in a file.  The template
        engine and the LLM both emit it inside the plugin body (after the
        docstring), which pushes it to line ~20 and causes a SyntaxError in
        the sandbox subprocess.

        This method is the single owner of the file header.  It:
          1. Strips ALL existing `from __future__ import annotations` lines
             from the plugin body so there is never a duplicate.
          2. Extracts the module docstring (if present) from the remaining body.
          3. Assembles: future-import → bootstrap → docstring → body.

        The project root is three levels up from plugin_dir:
          plugins/installed/<plugin_name>/   ← plugin_dir
          plugins/installed/                 ← dirname(plugin_dir)
          plugins/                           ← dirname(dirname(...))
          <project root>                     ← dirname(dirname(dirname(...)))
        """
        import re as _re

        # ── 1. Guard: don't double-inject the bootstrap ───────────────────────
        already_patched = "_project_root" in plugin_code

        # ── 2. Strip every `from __future__ import annotations` from the body.
        #       The template header and LLM both add one; we own it now.
        future_line_re = _re.compile(
            r"^[ \t]*from\s+__future__\s+import\s+annotations[ \t]*\n?",
            _re.MULTILINE,
        )
        clean_body = future_line_re.sub("", plugin_code)

        # ── 3. Extract module docstring so it stays readable after the header.
        #       Python allows a docstring after `from __future__` imports.
        docstring = ""
        body_after_doc = clean_body.lstrip("\n")

        if body_after_doc.startswith('"""') or body_after_doc.startswith("'''"):
            quote = body_after_doc[:3]
            end_idx = body_after_doc.find(quote, 3)
            if end_idx != -1:
                end_idx += 3          # include closing quotes
                docstring    = body_after_doc[:end_idx].rstrip()
                body_after_doc = body_after_doc[end_idx:].lstrip("\n")

        # ── 4. Build the sys.path bootstrap (skip if already present) ─────────
        if already_patched:
            bootstrap = ""
        else:
            bootstrap = (
                "import sys as _sys, os as _os\n"
                "_plugin_dir = _os.path.abspath(_os.path.dirname(__file__))\n"
                "_project_root = _os.path.dirname(\n"
                "    _os.path.dirname(_os.path.dirname(_plugin_dir)))\n"
                "if _project_root not in _sys.path:\n"
                "    _sys.path.insert(0, _project_root)\n"
            )

        # ── 5. Assemble final file ────────────────────────────────────────────
        #   from __future__ import annotations   ← MUST be line 1
        #   <bootstrap>                          ← sys.path fix
        #   <docstring>                          ← module docstring (optional)
        #   <body>                               ← rest of plugin code
        parts = ["from __future__ import annotations\n"]
        if bootstrap:
            parts.append(bootstrap)
        if docstring:
            parts.append("\n" + docstring + "\n")
        parts.append("\n" + body_after_doc)

        return "".join(parts)

    @staticmethod
    def _extract_last_json(stdout: str) -> str:
        """
        Given subprocess stdout that may contain debug prints before the JSON
        payload (e.g. 'Emitting summary event\\n{…}'), extract and return only
        the last complete JSON object or array in the string.

        This makes sandbox execution robust against plugins that accidentally
        print debug text before their JSON output.  The plugin should ideally
        be silent, but this guard means one stray print() won't fail the whole
        pipeline.

        Returns the extracted JSON string, or the original stdout unchanged if
        no balanced JSON object/array is found (so callers still get a
        meaningful error message for debugging).
        """
        text = (stdout or "").strip()
        for start_char, end_char in [('{', '}'), ('[', ']')]:
            pos = text.rfind(start_char)
            while pos >= 0:
                candidate = text[pos:]
                depth = 0
                in_string = False
                escape_next = False
                end_pos = -1
                for i, ch in enumerate(candidate):
                    if escape_next:
                        escape_next = False
                        continue
                    if ch == '\\' and in_string:
                        escape_next = True
                        continue
                    if ch == '"' and not escape_next:
                        in_string = not in_string
                        continue
                    if in_string:
                        continue
                    if ch == start_char:
                        depth += 1
                    elif ch == end_char:
                        depth -= 1
                        if depth == 0:
                            end_pos = i
                            break
                if end_pos >= 0:
                    return candidate[:end_pos + 1]
                pos = text.rfind(start_char, 0, pos)
        return stdout  # unchanged — caller sees original text for debugging


# Global instance
sandbox_runner = SandboxRunner()