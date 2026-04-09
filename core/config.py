import json
import logging
import os
from pathlib import Path
from typing import Dict, List, Optional

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
    AUDIO_INPUT_INDEX = None  # Default to system default mic, can be overridden by env var or config file 
    AUDIO_RATE = 16000 # Standard for AI models
    AUDIO_CHUNK = 512 # Required for openWakeWord to see full words
    WAKE_WORD = "alexa"
    WAKE_THRESHOLD = 0.50
    MIC_VOLUME_BOOST = 1.2

    # Capture behavior (post-wake)
    VOICE_CLEAR_BUFFER_CHUNKS = int(os.getenv("VOICE_CLEAR_BUFFER_CHUNKS", "1"))

    # Noise reduction (fan/room noise)
    VOICE_DENOISE_ENABLED = os.getenv("VOICE_DENOISE_ENABLED", "true").lower() in ("1", "true", "yes", "on")
    VOICE_DENOISE_PROP_DECREASE = float(os.getenv("VOICE_DENOISE_PROP_DECREASE", "0.65"))
    VOICE_DENOISE_N_STD = float(os.getenv("VOICE_DENOISE_N_STD", "1.5"))

    # --- STT (Speech-to-Text) Settings ---
    # ⚠️ Using 'medium' - best accuracy for local STT (large models require GPU)
    # Available sizes: 'tiny', 'base', 'small', 'medium', 'large-v2', 'large-v3'
    STT_MODEL_SIZE = os.getenv("STT_MODEL_SIZE", "medium")
    # ⚠️ Increased beam_size from 5 to 10 - maximum accuracy
    STT_BEAM_SIZE = int(os.getenv("STT_BEAM_SIZE", "10"))
    STT_BEST_OF = int(os.getenv("STT_BEST_OF", "10"))
    STT_TEMPERATURE = float(os.getenv("STT_TEMPERATURE", "0.0"))
    STT_LANGUAGE = os.getenv("STT_LANGUAGE", "en")
    # ⚠️ Enabled initial prompt for better context understanding
    STT_USE_INITIAL_PROMPT = os.getenv("STT_USE_INITIAL_PROMPT", "true").lower() in ("1", "true", "yes", "on")
    STT_INITIAL_PROMPT_MAX_WORDS = int(os.getenv("STT_INITIAL_PROMPT_MAX_WORDS", "40"))
    STT_INITIAL_PROMPT_MODE = os.getenv("STT_INITIAL_PROMPT_MODE", "capabilities")  # minimal | capabilities

    # ⚠️ Enable audio normalization for consistent volume levels
    STT_NORMALIZE_AUDIO = os.getenv("STT_NORMALIZE_AUDIO", "true").lower() in ("1", "true", "yes", "on")

    # STT provider mode: local | hybrid | cloud
    # ⚠️ Changed to 'hybrid' - uses local STT, falls back to cloud when confidence is low
    STT_PROVIDER = os.getenv("STT_PROVIDER", "hybrid")
    # ⚠️ Lowered threshold from 0.45 to 0.35 - more aggressive cloud fallback
    STT_MIN_CONFIDENCE = float(os.getenv("STT_MIN_CONFIDENCE", "0.35"))
    CLOUD_STT_PROVIDER = os.getenv("CLOUD_STT_PROVIDER", "openai")
    CLOUD_STT_TIMEOUT_SECONDS = float(os.getenv("CLOUD_STT_TIMEOUT_SECONDS", "15"))

    # Optional OpenAI STT config (used only when STT_PROVIDER enables cloud)
    OPENAI_API_KEY: str = os.getenv("OPENAI_API_KEY", "")
    OPENAI_BASE_URL: str = os.getenv("OPENAI_BASE_URL", "https://api.openai.com/v1")
    OPENAI_STT_MODEL: str = os.getenv("OPENAI_STT_MODEL", "gpt-4o-mini-transcribe")


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