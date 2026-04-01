import logging
import os
from core.config import settings
from core.event_bus import bus
# We import your brain client to actually communicate with the LLMs!
from brain.llm_client import llm_client

class AutoFixer:
    """🛠️ The self-healing engineer of the AI OS.
    
    Takes a parsed error report, reads the source code, asks the LLM for a 
    solution, and applies the patch.
    """
    def __init__(self):
        self.logger = logging.getLogger("AutoFixer")
        self.max_attempts = 3  # Prevent infinite debug loops!

    async def attempt_fix(self, parsed_report: dict):
        """Processes the error report and attempts to rewrite the broken file."""
        file_path = parsed_report.get("file")
        error_type = parsed_report.get("error_type")
        message = parsed_report.get("message")
        
        # Guard rails: Don't try to fix if there is no file associated
        if not file_path or not os.path.exists(file_path):
            self.logger.warning(f"Cannot auto-fix. File not found or invalid: {file_path}")
            return

        # Guard rails: Prevent editing core system files for safety
        if any(restricted in file_path for restricted in settings.RESTRICTED_PATHS):
            self.logger.warning(f"🛡️ Auto-fix blocked! Attempted to modify a restricted path: {file_path}")
            return

        self.logger.info(f"🔧 Attempting to auto-fix {error_type} in {file_path}...")

        try:
            # 1. Read the broken code
            with open(file_path, "r") as f:
                original_code = f.read()

            # 2. Build a highly specific prompt (Zero Hardcoding!)
            prompt = self._build_fix_prompt(file_path, original_code, parsed_report)

            # 3. Call the LLM (This is where your API keys in config.py are finally used!)
            self.logger.info(f"🧠 Consulting LLM ({settings.DEFAULT_LLM}) for a solution...")
            
            # We use the default LLM specified in your config (like gpt-4o)
            suggested_code = await llm_client.generate(prompt, model=settings.DEFAULT_LLM)

            if not suggested_code or "```python" not in suggested_code:
                self.logger.error("LLM failed to return a valid code block for the fix.")
                return

            # 4. Extract the clean code from the markdown response
            clean_code = self._extract_code(suggested_code)

            # 5. Apply the fix!
            with open(file_path, "w") as f:
                f.write(clean_code)

            self.logger.info(f"✅ Successfully patched {file_path}. Prompting system to re-run.")
            
            # 6. Alert the system that a fix was applied so it can test it!
            bus.publish(
                "fix_applied", 
                {"file": file_path, "error_type": error_type}, 
                source="auto_fix"
            )

        except Exception as e:
            self.logger.error(f"Failed to execute auto-fix: {e}")

    