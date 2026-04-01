import logging
import os
from core.config import settings
from core.event_bus import bus
from brain.llm_client import llm_client

# 🔄 Externalized helpers mapped to the new architecture
from debugging.rollback_manager import rollback_manager
from debugging.fix_validator import fix_validator


class AutoFixer:
    """🛠️ Advanced Self-Healing Engine.

    Orchestrates the loop: DeepSeek (Gen) -> Gemini (Critique via Validator) ->
    Pytest (Execution).
    """

    def __init__(self):
        self.logger = logging.getLogger("AutoFixer")
        self.max_attempts = 3

    async def attempt_fix(self, parsed_report: dict):
        file_path = parsed_report.get("file")

        # 1. Guard rails
        if not file_path or not os.path.exists(file_path):
            self.logger.warning(f"Invalid file: {file_path}")
            return

        if any(r in file_path for r in settings.RESTRICTED_PATHS):
            self.logger.warning(f"🛡️ Restricted file blocked: {file_path}")
            return

        self.logger.info(f"🔧 Starting self-healing for {file_path}")

        # 💾 2. Backup using Rollback Manager
        backup_path = rollback_manager.create_backup(file_path)

        try:
            with open(file_path, "r") as f:
                code = f.read()

            # 3. Enter the multi-attempt fixing loop
            for attempt in range(self.max_attempts):
                self.logger.info(
                    f"⚡ Attempt {attempt+1}/{self.max_attempts}"
                )

                # 🧠 GENERATE FIX (DeepSeek via LLMClient)
                gen_prompt = self._build_fix_prompt(
                    file_path, code, parsed_report
                )
                response = await llm_client.generate(gen_prompt)
                new_code = self._extract_code(response)

                if not new_code:
                    self.logger.warning("No code generated.")
                    continue

                # ✨ CRITIC REVIEW (Gemini via external FixValidator)
                audit = await fix_validator.validate_fix(
                    file_path, new_code, parsed_report.get("message")
                )

                if not audit.get("valid", False):
                    self.logger.warning(
                        f"❌ Critic rejected: {audit.get('reason')}"
                    )

                    # 🧠 Apply feedback and loop back to DeepSeek
                    code = self._apply_feedback(code, audit)
                    continue

                # 💾 APPLY PROPOSED FIX
                with open(file_path, "w") as f:
                    f.write(new_code)

                # 🧪 RUN TESTS (Executor)
                success, error_output = self._run_tests(file_path)

                if success:
                    self.logger.info("🔥 FIX SUCCESSFUL!")
                    await bus.publish(
                        "fix_applied",
                        {"file": file_path, "status": "verified"},
                        source="auto_fix",
                    )
                    return

                self.logger.warning(
                    "💥 Tests failed, retrying with execution feedback..."
                )

                # 🧠 Feed failing terminal output back into the loop
                code = f"""
Previous code failed tests.

ERROR:
{error_output}

Fix the issues in this code:
{new_code}
"""

            # ❌ ALL ATTEMPTS FAILED → Auto-execute Rollback
            self.logger.error("🚨 All fix attempts failed. Rolling back...")
            rollback_manager.restore_backup(file_path, backup_path)

        except Exception as e:
            self.logger.error(f"Critical failure: {e}")
            rollback_manager.restore_backup(file_path, backup_path)

    # ---------------- HELPERS ---------------- #

    def _apply_feedback(self, code, audit):
        """Builds a prompt inserting Gemini's rejection notes."""
        return f"""
The previous code was rejected by the auditor.

REASON:
{audit.get('reason')}

SUGGESTIONS:
{audit.get('suggested_tweaks')}

Fix this code:
{code}
"""

    def _run_tests(self, file_path):
        """Runs pytest and returns (success_boolean, output_string)."""
        try:
            import subprocess

            result = subprocess.run(
                ["pytest", file_path],
                capture_output=True,
                text=True,
                timeout=30,
            )
            return result.returncode == 0, result.stderr + result.stdout
        except Exception as e:
            return False, str(e)

    def _extract_code(self, response: str) -> str:
        """Pulls raw python code out of LLM markdown wrappers."""
        if "```python" in response:
            return response.split("```python")[1].split("```")[0].strip()
        return response.strip()

    def _build_fix_prompt(self, file_path, code, report):
        """Prepares a clear engineering prompt for DeepSeek."""
        return f"""
Fix this Python file.

Error:
{report.get('message')}

Code:
{code}

Return ONLY fixed code in ```python block.
"""