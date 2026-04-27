from __future__ import annotations

import json
import logging
import os
from pathlib import Path
from typing import Dict, List, Optional

from dotenv import load_dotenv

# Load .env from the project root (two levels up from core/config.py).
# Must happen before any os.getenv() call so all keys are present.
load_dotenv(dotenv_path=Path(__file__).resolve().parent.parent / ".env", override=True)

logger = logging.getLogger("Config")


class Settings:
    """Central configuration for the Operonix AI OS Agent.

    All model names, API keys, thresholds, and file paths live here.
    Nothing else in the codebase should hardcode these values.
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

    VOICE_CLEAR_BUFFER_CHUNKS: int = int(os.getenv("VOICE_CLEAR_BUFFER_CHUNKS", "1"))

    VOICE_DENOISE_ENABLED: bool = os.getenv("VOICE_DENOISE_ENABLED", "true").lower() in (
        "1", "true", "yes", "on"
    )
    VOICE_DENOISE_PROP_DECREASE: float = float(os.getenv("VOICE_DENOISE_PROP_DECREASE", "0.65"))
    VOICE_DENOISE_N_STD: float = float(os.getenv("VOICE_DENOISE_N_STD", "1.5"))

    # --- STT Settings ---
    STT_MODEL_SIZE: str = os.getenv("STT_MODEL_SIZE", "base")
    STT_BEST_OF: int = int(os.getenv("STT_BEST_OF", "10"))
    STT_BEAM_SIZE: int = int(os.getenv("STT_BEAM_SIZE", "5"))
    STT_TEMPERATURE: float = float(os.getenv("STT_TEMPERATURE", "0.0"))
    STT_LANGUAGE: str = os.getenv("STT_LANGUAGE", "en")
    STT_USE_INITIAL_PROMPT: bool = os.getenv("STT_USE_INITIAL_PROMPT", "true").lower() in (
        "1", "true", "yes", "on"
    )
    STT_INITIAL_PROMPT_MAX_WORDS: int = int(os.getenv("STT_INITIAL_PROMPT_MAX_WORDS", "40"))
    STT_INITIAL_PROMPT_MODE: str = os.getenv("STT_INITIAL_PROMPT_MODE", "capabilities")
    STT_NORMALIZE_AUDIO: bool = os.getenv("STT_NORMALIZE_AUDIO", "true").lower() in (
        "1", "true", "yes", "on"
    )
    STT_PROVIDER: str = os.getenv("STT_PROVIDER", "hybrid")
    STT_MIN_CONFIDENCE: float = float(os.getenv("STT_MIN_CONFIDENCE", "0.35"))
    CLOUD_STT_PROVIDER: str = os.getenv("CLOUD_STT_PROVIDER", "openai")
    CLOUD_STT_TIMEOUT_SECONDS: float = float(os.getenv("CLOUD_STT_TIMEOUT_SECONDS", "15"))

    # OpenAI (used for cloud STT)
    OPENAI_API_KEY: str = os.getenv("OPENAI_API_KEY", "")
    OPENAI_BASE_URL: str = os.getenv("OPENAI_BASE_URL", "https://api.openai.com/v1")
    OPENAI_STT_MODEL: str = os.getenv("OPENAI_STT_MODEL", "gpt-4o-mini-transcribe")

    # --- File & Directory Paths ---
    LOGS_DIR: Path = BASE_DIR / "logs"
    SANDBOX_DIR: Path = BASE_DIR / "sandbox"
    PLUGINS_DIR: Path = BASE_DIR / "plugins"
    DYNAMIC_SETTINGS_FILE: Path = BASE_DIR / "core" / "dynamic_settings.json"

    # --- API KEYS ---
    OPENROUTER_API_KEY: str = os.getenv("OPENROUTER_API_KEY", "")
    GEMINI_API_KEY: str = os.getenv("GEMINI_API_KEY", "")

    # ── LLM / Model Settings ──────────────────────────────────────────────────

    # Groq (cloud, fast inference)
    # ----------------------------
    # Groq offers free-tier access with very fast inference via Llama3, Mixtral, Gemma.
    #
    # Available models (as of 2026):
    #   llama3-70b-8192       ← best quality, recommended default
    #   llama3-8b-8192        ← faster, lower quality
    #   mixtral-8x7b-32768    ← large context window
    #   gemma2-9b-it          ← Google Gemma 2
    #
    # Get your free API key at: https://console.groq.com
    # Set in .env:  GROQ_API_KEY=gsk_...
    #               GROQ_MODEL=llama3-70b-8192
    GROQ_API_KEY: str = os.getenv("GROQ_API_KEY", "")
    GROQ_MODEL: str = os.getenv("GROQ_MODEL", "llama3-70b-8192")
    GROQ_TIMEOUT: int = int(os.getenv("GROQ_TIMEOUT", "30"))

    # Ollama (local) — kept as fallback; set OLLAMA_ENABLED=false to disable
    # -----------------------------------------------------------------------
    OLLAMA_BASE_URL: str = os.getenv("OLLAMA_BASE_URL", "http://localhost:11434")
    OLLAMA_MODEL: str = os.getenv("OLLAMA_MODEL", "llama3")
    OLLAMA_EMBED_MODEL: str = os.getenv("OLLAMA_EMBED_MODEL", "all-minilm")
    OLLAMA_TIMEOUT: int = int(os.getenv("OLLAMA_TIMEOUT", "60"))
    OLLAMA_ENABLED: bool = os.getenv("OLLAMA_ENABLED", "false").lower() in (
        "1", "true", "yes", "on"
    )

    # OpenRouter
    # ----------
    OPENROUTER_MODEL: str = os.getenv(
        "OPENROUTER_MODEL", "meta-llama/llama-3.3-70b-instruct:free"
    )

    # Gemini
    # ------
    GEMINI_MODEL: str = os.getenv("GEMINI_MODEL", "gemini-2.0-flash")

    # ─────────────────────────────────────────────────────────────────────────

    # --- System Guardrails ---
    MAX_RETRY_ATTEMPTS: int = 3
    SAFE_MODE: bool = True

    RESTRICTED_PATHS: List[str] = [
        str(BASE_DIR / "core"),
        str(BASE_DIR / "safety"),
    ]

    # --- Plugin Settings ---
    SANDBOX_TIMEOUT: int = 30
    SANDBOX_MEMORY_MB: int = 256
    GAP_CONSECUTIVE_THRESHOLD: int = 3
    GAP_WINDOW_THRESHOLD: int = 5
    PLUGIN_REVOKE_CONSECUTIVE: int = 5
    PLUGIN_EVOLVE_THRESHOLD: float = 0.75

    # --- Server & Dashboard ---
    API_HOST: str = "localhost"
    API_PORT: int = 8000

    # ── Panel Settings ────────────────────────────────────────────────────────
    PANEL_ENABLED: bool = os.getenv("PANEL_ENABLED", "true").lower() in (
        "1", "true", "yes", "on"
    )
    PANEL_START_TIMEOUT: float = float(os.getenv("PANEL_START_TIMEOUT", "5.0"))
    APP_CONTEXT_POLL_INTERVAL: float = float(os.getenv("APP_CONTEXT_POLL_INTERVAL", "2.0"))
    INTENT_MATCH_MIN_CONFIDENCE: float = float(os.getenv("INTENT_MATCH_MIN_CONFIDENCE", "0.35"))
    PRUNE_TIMEOUT: float = float(os.getenv("PRUNE_TIMEOUT", "2.0"))

    # ── Input Mode ────────────────────────────────────────────────────────────
    CURRENT_MODE: str = os.getenv("CURRENT_MODE", "panel")
    MODE_SWITCH_DRAIN_TIMEOUT: float = float(os.getenv("MODE_SWITCH_DRAIN_TIMEOUT", "30.0"))

    def __init__(self):
        for path in [self.LOGS_DIR, self.SANDBOX_DIR]:
            path.mkdir(parents=True, exist_ok=True)

        self.RISKY_INTENTS: List[str] = ["file_delete", "shell_command", "run_command"]
        self.COMPLEX_INTENTS: List[str] = ["write_", "debug_", "complex_"]

        self._load_dynamic_settings()
        self._warn_missing_keys()

    def _load_dynamic_settings(self):
        """Safely loads dynamic intent configurations from JSON."""
        if not self.DYNAMIC_SETTINGS_FILE.exists():
            logger.info(
                "No dynamic_settings.json found. Using hardcoded fallback defaults."
            )
            return

        try:
            with open(self.DYNAMIC_SETTINGS_FILE, "r") as f:
                data = json.load(f)
                self.RISKY_INTENTS = data.get("risky_intents", self.RISKY_INTENTS)
                self.COMPLEX_INTENTS = data.get("complex_intents", self.COMPLEX_INTENTS)
                logger.info("Successfully loaded dynamic intent configurations.")
        except json.JSONDecodeError:
            logger.error(
                "🚨 Corrupted dynamic_settings.json detected! Safe defaults used."
            )
        except Exception as exc:
            logger.error("Failed to load dynamic settings: %s. Using defaults.", exc)

    def _warn_missing_keys(self):
        """Warn about missing API keys at startup so issues surface immediately."""
        # Groq warnings (primary provider)
        if not self.GROQ_API_KEY:
            logger.warning(
                "⚠️  GROQ_API_KEY is not set. "
                "LLM calls will fall back to OpenRouter or Ollama. "
                "Get a free key at https://console.groq.com and set GROQ_API_KEY in your .env"
            )
        else:
            logger.info(
                "✅ Groq provider ready — model: %s", self.GROQ_MODEL
            )

        if not self.OPENROUTER_API_KEY:
            logger.warning(
                "⚠️  OPENROUTER_API_KEY is not set. "
                "OpenRouter fallback will be skipped."
            )

        if not self.GEMINI_API_KEY:
            logger.info(
                "ℹ️  GEMINI_API_KEY not set — Gemini provider will be skipped."
            )

        if not self.OLLAMA_ENABLED:
            logger.info(
                "ℹ️  Ollama is disabled (OLLAMA_ENABLED=false). "
                "Set OLLAMA_ENABLED=true in .env if you want a local fallback."
            )

        logger.info(
            "LLM priority — Groq: %s | OpenRouter: %s | Gemini: %s | Ollama: %s",
            self.GROQ_MODEL if self.GROQ_API_KEY else "(no key)",
            self.OPENROUTER_MODEL if self.OPENROUTER_API_KEY else "(no key)",
            self.GEMINI_MODEL if self.GEMINI_API_KEY else "(no key)",
            self.OLLAMA_MODEL if self.OLLAMA_ENABLED else "(disabled)",
        )


# Global instance
settings = Settings()