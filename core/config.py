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
    #
    # Ollama (local)
    # --------------
    # OLLAMA_MODEL must match the name shown by `ollama list`.
    # Common values: llama3, llama3.2, mistral, phi4, qwen2.5
    # Set in .env:  OLLAMA_MODEL=llama3
    OLLAMA_BASE_URL: str = os.getenv("OLLAMA_BASE_URL", "http://localhost:11434")
    OLLAMA_MODEL: str = os.getenv("OLLAMA_MODEL", "llama3")
    OLLAMA_EMBED_MODEL: str = os.getenv("OLLAMA_EMBED_MODEL", "all-minilm")
    OLLAMA_TIMEOUT: int = int(os.getenv("OLLAMA_TIMEOUT", "60"))
    OLLAMA_ENABLED: bool = True

    # OpenRouter
    # ----------
    # FIX: Model slug is now fully configurable — never hardcoded.
    # The old hardcoded "deepseek/deepseek-r1-distill-qwen-14b" was removed
    # from OpenRouter, causing all cloud LLM calls to 404.
    #
    # Free models available on OpenRouter (as of April 2026):
    #   meta-llama/llama-3.1-8b-instruct:free
    #   mistralai/mistral-7b-instruct:free
    #   google/gemma-3-27b-it:free
    #   deepseek/deepseek-chat-v3-0324:free
    #
    # Set in .env:  OPENROUTER_MODEL=meta-llama/llama-3.1-8b-instruct:free
    OPENROUTER_MODEL: str = os.getenv(
        "OPENROUTER_MODEL", "meta-llama/llama-3.1-8b-instruct:free"
    )

    # Gemini
    # ------
    # Set in .env:  GEMINI_MODEL=gemini-2.0-flash
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
    # Valid values: "voice" | "panel" | "none"
    # Default: "panel" — ModeManager reads this at boot.
    # Never read this directly at runtime; use mode_manager.current_mode instead.
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
        if not self.OPENROUTER_API_KEY:
            logger.warning(
                "⚠️  OPENROUTER_API_KEY is not set. "
                "All LLM calls will fall back to local Ollama. "
                "Set OPENROUTER_API_KEY and OPENROUTER_MODEL in your .env to enable cloud inference."
            )
        elif not self.OPENROUTER_MODEL:
            logger.warning(
                "⚠️  OPENROUTER_MODEL is not set. "
                "OpenRouter calls will be skipped even though an API key is present. "
                "Set OPENROUTER_MODEL=<slug> in your .env (e.g. meta-llama/llama-3.1-8b-instruct:free)."
            )

        if not self.GEMINI_API_KEY:
            logger.info(
                "ℹ️  GEMINI_API_KEY not set — Gemini provider will be skipped."
            )

        logger.info(
            "LLM config — Ollama: %s @ %s | OpenRouter: %s | Gemini: %s",
            self.OLLAMA_MODEL,
            self.OLLAMA_BASE_URL,
            self.OPENROUTER_MODEL or "(not configured)",
            self.GEMINI_MODEL if self.GEMINI_API_KEY else "(no key)",
        )


# Global instance
settings = Settings()