import json
import logging
import os
from pathlib import Path
from typing import Dict, List, Optional

from dotenv import load_dotenv

# Load .env from the project root (two levels up from core/config.py).
# This must happen before ANY os.getenv() call so all keys are available.
load_dotenv(dotenv_path=Path(__file__).resolve().parent.parent / ".env", override=True)

logger = logging.getLogger("Config")


class Settings:
    """Central configuration for the Operonix AI OS Agent.

    Holds environment variables, model choices, safety thresholds, and file
    paths. Reads dynamic intents from a secure JSON file to prevent AI syntax
    crashes.
    """

    # --- PROJECT PATHS ---
    BASE_DIR: Path = Path(__file__).resolve().parent.parent

    # --- Voice & Audio Settings ---
    AUDIO_INPUT_INDEX = None
    AUDIO_RATE = 16000
    AUDIO_CHUNK = 512
    WAKE_WORD = "alexa"
    WAKE_THRESHOLD = 0.50
    MIC_VOLUME_BOOST = 1.2

    VOICE_CLEAR_BUFFER_CHUNKS = int(os.getenv("VOICE_CLEAR_BUFFER_CHUNKS", "1"))

    VOICE_DENOISE_ENABLED = os.getenv("VOICE_DENOISE_ENABLED", "true").lower() in ("1", "true", "yes", "on")
    VOICE_DENOISE_PROP_DECREASE = float(os.getenv("VOICE_DENOISE_PROP_DECREASE", "0.65"))
    VOICE_DENOISE_N_STD = float(os.getenv("VOICE_DENOISE_N_STD", "1.5"))

    # --- STT Settings ---
    STT_MODEL_SIZE = os.getenv("STT_MODEL_SIZE", "base")
    STT_BEST_OF = int(os.getenv("STT_BEST_OF", "10"))
    STT_BEAM_SIZE = int(os.getenv("STT_BEAM_SIZE", "5"))
    STT_TEMPERATURE = float(os.getenv("STT_TEMPERATURE", "0.0"))
    STT_LANGUAGE = os.getenv("STT_LANGUAGE", "en")
    STT_USE_INITIAL_PROMPT = os.getenv("STT_USE_INITIAL_PROMPT", "true").lower() in ("1", "true", "yes", "on")
    STT_INITIAL_PROMPT_MAX_WORDS = int(os.getenv("STT_INITIAL_PROMPT_MAX_WORDS", "40"))
    STT_INITIAL_PROMPT_MODE = os.getenv("STT_INITIAL_PROMPT_MODE", "capabilities")
    STT_NORMALIZE_AUDIO = os.getenv("STT_NORMALIZE_AUDIO", "true").lower() in ("1", "true", "yes", "on")
    STT_PROVIDER = os.getenv("STT_PROVIDER", "hybrid")
    STT_MIN_CONFIDENCE = float(os.getenv("STT_MIN_CONFIDENCE", "0.35"))
    CLOUD_STT_PROVIDER = os.getenv("CLOUD_STT_PROVIDER", "openai")
    CLOUD_STT_TIMEOUT_SECONDS = float(os.getenv("CLOUD_STT_TIMEOUT_SECONDS", "15"))

    OPENAI_API_KEY: str = os.getenv("OPENAI_API_KEY", "")
    OPENAI_BASE_URL: str = os.getenv("OPENAI_BASE_URL", "https://api.openai.com/v1")
    OPENAI_STT_MODEL: str = os.getenv("OPENAI_STT_MODEL", "gpt-4o-mini-transcribe")

    LOGS_DIR: Path = BASE_DIR / "logs"
    SANDBOX_DIR: Path = BASE_DIR / "sandbox"
    PLUGINS_DIR: Path = BASE_DIR / "plugins"

    DYNAMIC_SETTINGS_FILE: Path = BASE_DIR / "core" / "dynamic_settings.json"

    # --- API KEYS & EXTERNAL SERVICES ---
    OPENROUTER_API_KEY: str = os.getenv("OPENROUTER_API_KEY", "")
    GEMINI_API_KEY: str = os.getenv("GEMINI_API_KEY", "")

    # --- BRAIN & LLM SETTINGS ---
    OLLAMA_EMBED_MODEL: str = "all-minilm"
    OLLAMA_ENABLED: bool = True
    OLLAMA_MODEL: str = "llama3"
    OLLAMA_BASE_URL: str = "http://localhost:11434"
    OLLAMA_TIMEOUT: int = 30

    # --- SYSTEM GUARDRAILS ---
    MAX_RETRY_ATTEMPTS: int = 3
    SAFE_MODE: bool = True

    RESTRICTED_PATHS: List[str] = [
        str(BASE_DIR / "core"),
        str(BASE_DIR / "safety"),
    ]

    # --- PLUGIN SETTINGS ---
    SANDBOX_TIMEOUT = 30
    SANDBOX_MEMORY_MB = 256
    GAP_CONSECUTIVE_THRESHOLD = 3
    GAP_WINDOW_THRESHOLD = 5
    PLUGIN_REVOKE_CONSECUTIVE = 5
    PLUGIN_EVOLVE_THRESHOLD = 0.75

    # --- SERVER & DASHBOARD ---
    API_HOST: str = "localhost"
    API_PORT: int = 8000

    # ── Panel Settings ────────────────────────────────────────────────────────
    PANEL_ENABLED: bool = os.getenv("PANEL_ENABLED", "true").lower() in ("1", "true", "yes", "on")
    PANEL_START_TIMEOUT: float = float(os.getenv("PANEL_START_TIMEOUT", "5.0"))
    APP_CONTEXT_POLL_INTERVAL: float = float(os.getenv("APP_CONTEXT_POLL_INTERVAL", "2.0"))
    INTENT_MATCH_MIN_CONFIDENCE: float = float(os.getenv("INTENT_MATCH_MIN_CONFIDENCE", "0.35"))
    PRUNE_TIMEOUT: float = float(os.getenv("PRUNE_TIMEOUT", "2.0"))

    # ── Input Mode ────────────────────────────────────────────────────────────
    # Persisted between restarts via .env key CURRENT_MODE.
    # Valid values: "voice" | "panel" | "none"
    # Default is "panel" — ModeManager reads this at boot and applies it.
    # Never read this directly at runtime; use mode_manager.current_mode instead.
    CURRENT_MODE: str = os.getenv("CURRENT_MODE", "panel")

    # How long (seconds) a mode switch waits for an active task to finish
    # before switching anyway. Configurable without restarting.
    MODE_SWITCH_DRAIN_TIMEOUT: float = float(os.getenv("MODE_SWITCH_DRAIN_TIMEOUT", "30.0"))
    # ─────────────────────────────────────────────────────────────────────────

    def __init__(self):
        for path in [self.LOGS_DIR, self.SANDBOX_DIR]:
            path.mkdir(parents=True, exist_ok=True)

        self.RISKY_INTENTS: List[str] = ["file_delete", "shell_command", "run_command"]
        self.COMPLEX_INTENTS: List[str] = ["write_", "debug_", "complex_"]

        self._load_dynamic_settings()

        if not self.OPENROUTER_API_KEY:
            logger.warning(
                "⚠️  OPENROUTER_API_KEY is not set. DeepSeek via OpenRouter will "
                "be unavailable and all LLM calls will fall back to local Ollama. "
                "Set OPENROUTER_API_KEY in your .env file to enable cloud inference."
            )

    def _load_dynamic_settings(self):
        """Safely loads dynamic intent configurations from JSON."""
        if not self.DYNAMIC_SETTINGS_FILE.exists():
            logger.info("No dynamic_settings.json found. Using hardcoded fallback defaults.")
            return

        try:
            with open(self.DYNAMIC_SETTINGS_FILE, "r") as f:
                data = json.load(f)
                self.RISKY_INTENTS = data.get("risky_intents", self.RISKY_INTENTS)
                self.COMPLEX_INTENTS = data.get("complex_intents", self.COMPLEX_INTENTS)
                logger.info("Successfully loaded dynamic intent configurations.")
        except json.JSONDecodeError:
            logger.error("🚨 Corrupted dynamic_settings.json detected! Safe defaults used.")
        except Exception as e:
            logger.error(f"Failed to load dynamic settings: {e}. Using defaults.")


# Global instance
settings = Settings()