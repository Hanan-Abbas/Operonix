import json
import logging
import os
from pathlib import Path
from typing import Dict, List

logger = logging.getLogger("Config")


class Settings:
    """Central configuration for the Operonix AI OS Agent.

    Holds environment variables, model choices, safety thresholds, and file
    paths. Reads dynamic intents from a secure JSON file to prevent AI syntax
    crashes.
    """

    # --- PROJECT PATHS ---
    BASE_DIR: Path = Path(__file__).resolve().parent.parent
    AUDIO_INPUT_INDEX = 2  # 🟢 FIX: Set this to the correct index for your microphone (use pyaudio to list devices)
    LOGS_DIR: Path = BASE_DIR / "logs"
    SANDBOX_DIR: Path = BASE_DIR / "sandbox"
    PLUGINS_DIR: Path = BASE_DIR / "plugins"

    # File where the AI is allowed to save new learned categories safely
    DYNAMIC_SETTINGS_FILE: Path = BASE_DIR / "core" / "dynamic_settings.json"

    # --- 🔄 API KEYS & EXTERNAL SERVICES ---
    # Swapped OpenAI/Anthropic out for the ones your LLMClient actually calls!
    DEEPSEEK_API_KEY: str = os.getenv("DEEPSEEK_API_KEY", "")
    GEMINI_API_KEY: str = os.getenv("GEMINI_API_KEY", "")

    # --- BRAIN & LLM SETTINGS ---
    # Removed gpt-4o variables. Your LLMClient is now provider-routed!
    OLLAMA_EMBED_MODEL: str = "all-minilm"

    # --- SYSTEM GUARDRAILS ---
    MAX_RETRY_ATTEMPTS: int = 3
    SAFE_MODE: bool = True

    RESTRICTED_PATHS: List[str] = [
        str(BASE_DIR / "core"),
        str(BASE_DIR / "safety"),
    ]

    # --- 🔄 SERVER & DASHBOARD ---
    # Pointing to full localhost is standard for FastAPI + WebSocket setups
    API_HOST: str = "localhost"
    API_PORT: int = 8000

    def __init__(self):
        # 1. Automatically ensure required system directories exist on startup
        for path in [self.LOGS_DIR, self.SANDBOX_DIR]:
            path.mkdir(parents=True, exist_ok=True)

        # 2. 🔄 Zero-Hardcoded Fallback Defaults
        # Notice we are using prefix concepts rather than exact rigid matches!
        self.RISKY_INTENTS: List[str] = ["file_delete", "shell_command", "run_command"]
        self.COMPLEX_INTENTS: List[str] = ["write_", "debug_", "complex_"]

        # 3. Load dynamic settings from JSON if the file exists!
        self._load_dynamic_settings()

    def _load_dynamic_settings(self):
        """Safely loads dynamic intent configurations from JSON.

        If the file is corrupted or missing, it falls back to system defaults.
        """
        if not self.DYNAMIC_SETTINGS_FILE.exists():
            logger.info(
                "No dynamic_settings.json found. Using hardcoded fallback defaults."
            )
            return

        try:
            with open(self.DYNAMIC_SETTINGS_FILE, "r") as f:
                data = json.load(f)
                self.RISKY_INTENTS = data.get(
                    "risky_intents", self.RISKY_INTENTS
                )
                self.COMPLEX_INTENTS = data.get(
                    "complex_intents", self.COMPLEX_INTENTS
                )
                logger.info("Successfully loaded dynamic intent configurations.")
        except json.JSONDecodeError:
            logger.error(
                "🚨 Corrupted dynamic_settings.json detected! Safe defaults used."
            )
        except Exception as e:
            logger.error(
                f"Failed to load dynamic settings: {e}. Using defaults."
            )


# Global instance
settings = Settings()