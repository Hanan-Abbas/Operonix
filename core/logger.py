"""
core/config.py — Operonix AI OS Agent Configuration
=====================================================
All settings are driven by environment variables with sensible cross-platform
defaults.  Nothing is hard-coded to a specific machine.

Quick-start overrides (put these in a .env file or export them):

  AUDIO_INPUT_INDEX=-1          # -1 = OS default mic; set to 0,1,2… for a specific device
  STT_MODEL_SIZE=small          # tiny | base | small | medium | large-v2 | large-v3
  WAKE_WORD=alexa               # any word supported by openWakeWord
  DEEPFILTER_ENABLED=true       # enable DeepFilterNet noise suppression
  STT_PROVIDER=hybrid           # local | hybrid | cloud
"""

from __future__ import annotations

import json
import logging
import os
from pathlib import Path
from typing import List, Optional

logger = logging.getLogger("Config")

# ---------------------------------------------------------------------------
# Helpers
# ---------------------------------------------------------------------------

def _bool(key: str, default: bool) -> bool:
    val = os.getenv(key, "")
    if not val:
        return default
    return val.strip().lower() in ("1", "true", "yes", "on")


def _int(key: str, default: int) -> int:
    try:
        return int(os.getenv(key, str(default)))
    except (ValueError, TypeError):
        return default


def _float(key: str, default: float) -> float:
    try:
        return float(os.getenv(key, str(default)))
    except (ValueError, TypeError):
        return default


def _str(key: str, default: str) -> str:
    return os.getenv(key, default)


# ---------------------------------------------------------------------------
# Settings
# ---------------------------------------------------------------------------

class Settings:
    """Central, device-agnostic configuration for Operonix.

    Every tuneable is backed by an env-var so the same code runs on a
    Raspberry Pi, a gaming laptop, a headless server, or a Windows box
    without touching a single line.
    """

    # ------------------------------------------------------------------ #
    # PROJECT PATHS                                                        #
    # ------------------------------------------------------------------ #
    BASE_DIR: Path = Path(__file__).resolve().parent.parent
    LOGS_DIR: Path = BASE_DIR / "logs"
    SANDBOX_DIR: Path = BASE_DIR / "sandbox"
    PLUGINS_DIR: Path = BASE_DIR / "plugins"
    DYNAMIC_SETTINGS_FILE: Path = BASE_DIR / "core" / "dynamic_settings.json"

    # ------------------------------------------------------------------ #
    # AUDIO / MICROPHONE                                                   #
    # ------------------------------------------------------------------ #

    # Set to a non-negative integer to pin a specific device.
    # -1 (or unset) = let the OS choose the default input device.
    # Run `python -m voice.audio_devices` to list available indices.
    AUDIO_INPUT_INDEX: Optional[int] = (
        int(os.getenv("AUDIO_INPUT_INDEX", "-1"))
        if os.getenv("AUDIO_INPUT_INDEX") is not None
        else None
    )

    # 16000 Hz is required by Whisper, Silero VAD, and openWakeWord.
    # Do NOT change this unless you know all downstream models support it.
    AUDIO_RATE: int           = _int("AUDIO_RATE", 16000)

    # 512 samples = 32 ms @ 16 kHz — minimum for openWakeWord frame size.
    # Increase to 1024 or 1280 if you experience stream overflows on slow hardware.
    AUDIO_CHUNK: int          = _int("AUDIO_CHUNK", 512)

    # Multiplier applied after capture (1.0 = no change).
    MIC_VOLUME_BOOST: float   = _float("MIC_VOLUME_BOOST", 1.0)

    # ------------------------------------------------------------------ #
    # WAKE WORD                                                            #
    # ------------------------------------------------------------------ #
    WAKE_WORD: str            = _str("WAKE_WORD", "alexa")
    # Score threshold (0–1).  Lower = more sensitive but more false positives.
    WAKE_THRESHOLD: float     = _float("WAKE_THRESHOLD", 0.50)
    # Seconds to ignore further triggers after a successful detection.
    WAKE_COOLDOWN: float      = _float("WAKE_COOLDOWN", 3.0)

    # ------------------------------------------------------------------ #
    # VOICE CAPTURE (VAD + pipeline behaviour)                            #
    # ------------------------------------------------------------------ #
    # VAD speech probability threshold (0–1).  0.5 is a balanced default.
    VOICE_VAD_SPEECH_THRESHOLD: float = _float("VOICE_VAD_SPEECH_THRESHOLD", 0.50)

    # Maximum 512-sample VAD frames to process in one capture pass.
    # 300 frames × 32 ms = ~9.6 s hard ceiling.
    VOICE_CAPTURE_MAX_CHUNKS: int     = _int("VOICE_CAPTURE_MAX_CHUNKS", 300)

    # How many consecutive silent frames trigger end-of-utterance.
    # 50 × 32 ms ≈ 1.6 s of silence.  Raise on slow speakers, lower for snappy UX.
    VOICE_CAPTURE_SILENCE_CHUNKS: int = _int("VOICE_CAPTURE_SILENCE_CHUNKS", 50)

    # Pre-roll frames kept before speech onset (used as noise reference).
    VOICE_CAPTURE_PREROLL_CHUNKS: int = _int("VOICE_CAPTURE_PREROLL_CHUNKS", 8)

    # Stale-buffer flush after wake word (0 = disabled).
    VOICE_CLEAR_BUFFER_CHUNKS: int    = _int("VOICE_CLEAR_BUFFER_CHUNKS", 2)

    # Legacy VoiceListener compatibility
    MAX_RECORD_SECONDS: int           = _int("MAX_RECORD_SECONDS", 12)
    SILENCE_LIMIT: int                = _int("SILENCE_LIMIT", 80)

    # ------------------------------------------------------------------ #
    # NOISE REDUCTION                                                      #
    # ------------------------------------------------------------------ #

    # --- DeepFilterNet (preferred — neural, full-band suppression) ---
    # Requires: pip install deepfilternet
    # Automatically falls back to noisereduce if unavailable.
    DEEPFILTER_ENABLED: bool  = _bool("DEEPFILTER_ENABLED", True)

    # Attenuation limit in dB (0 = no suppression, 100 = maximum).
    # 60–80 is a good range for typical office/home noise.
    DEEPFILTER_ATTENUATION_LIMIT: float = _float("DEEPFILTER_ATTENUATION_LIMIT", 80.0)

    # --- noisereduce (spectral-gating fallback) ---
    VOICE_DENOISE_ENABLED: bool       = _bool("VOICE_DENOISE_ENABLED", True)
    VOICE_DENOISE_PROP_DECREASE: float = _float("VOICE_DENOISE_PROP_DECREASE", 0.65)
    VOICE_DENOISE_N_STD: float        = _float("VOICE_DENOISE_N_STD", 1.5)

    # ------------------------------------------------------------------ #
    # STT — Speech-to-Text (Faster-Whisper)                               #
    # ------------------------------------------------------------------ #

    # Model sizes (accuracy ↑, speed ↓, RAM ↑):
    #   tiny (39 M)  → base (74 M)  → small (244 M)
    #   medium (769 M) → large-v2/v3 (1.5 GB) — GPU recommended for large
    STT_MODEL_SIZE: str       = _str("STT_MODEL_SIZE", "small")

    # Whisper compute device.  "cpu" works everywhere; "cuda" needs an Nvidia GPU.
    STT_DEVICE: str           = _str("STT_DEVICE", "cpu")

    # int8 keeps RAM low on CPU.  Use float16 on GPU for speed.
    STT_COMPUTE_TYPE: str     = _str("STT_COMPUTE_TYPE", "int8")

    # Decoding settings.  Beam search values > 5 rarely help on "small".
    STT_BEAM_SIZE: int        = _int("STT_BEAM_SIZE", 5)
    STT_BEST_OF: int          = _int("STT_BEST_OF", 5)
    STT_TEMPERATURE: float    = _float("STT_TEMPERATURE", 0.0)
    STT_LANGUAGE: str         = _str("STT_LANGUAGE", "en")

    # Audio normalisation before transcription.
    STT_NORMALIZE_AUDIO: bool        = _bool("STT_NORMALIZE_AUDIO", True)
    STT_NORMALIZE_TARGET_DB: float   = _float("STT_NORMALIZE_TARGET_DB", -20.0)

    # Initial prompt — helps Whisper bias toward domain vocabulary.
    STT_USE_INITIAL_PROMPT: bool     = _bool("STT_USE_INITIAL_PROMPT", True)
    STT_INITIAL_PROMPT_MAX_WORDS: int = _int("STT_INITIAL_PROMPT_MAX_WORDS", 40)
    STT_INITIAL_PROMPT_MODE: str     = _str("STT_INITIAL_PROMPT_MODE", "capabilities")

    # ------------------------------------------------------------------ #
    # STT PROVIDER (local / hybrid / cloud)                               #
    # ------------------------------------------------------------------ #
    STT_PROVIDER: str               = _str("STT_PROVIDER", "hybrid")
    STT_MIN_CONFIDENCE: float       = _float("STT_MIN_CONFIDENCE", 0.40)
    CLOUD_STT_PROVIDER: str         = _str("CLOUD_STT_PROVIDER", "openai")
    CLOUD_STT_TIMEOUT_SECONDS: float = _float("CLOUD_STT_TIMEOUT_SECONDS", 15.0)

    # OpenAI-compatible STT endpoint
    OPENAI_API_KEY: str      = _str("OPENAI_API_KEY", "")
    OPENAI_BASE_URL: str     = _str("OPENAI_BASE_URL", "https://api.openai.com/v1")
    OPENAI_STT_MODEL: str    = _str("OPENAI_STT_MODEL", "gpt-4o-mini-transcribe")

    # ------------------------------------------------------------------ #
    # LLM / BRAIN                                                          #
    # ------------------------------------------------------------------ #
    DEEPSEEK_API_KEY: str    = _str("DEEPSEEK_API_KEY", "")
    GEMINI_API_KEY: str      = _str("GEMINI_API_KEY", "")
    OLLAMA_EMBED_MODEL: str  = _str("OLLAMA_EMBED_MODEL", "all-minilm")

    # ------------------------------------------------------------------ #
    # SERVER & DASHBOARD                                                   #
    # ------------------------------------------------------------------ #
    API_HOST: str  = _str("API_HOST", "localhost")
    API_PORT: int  = _int("API_PORT", 8000)

    # ------------------------------------------------------------------ #
    # SYSTEM GUARDRAILS                                                    #
    # ------------------------------------------------------------------ #
    MAX_RETRY_ATTEMPTS: int = _int("MAX_RETRY_ATTEMPTS", 3)
    SAFE_MODE: bool         = _bool("SAFE_MODE", True)

    RESTRICTED_PATHS: List[str] = [
        str(BASE_DIR / "core"),
        str(BASE_DIR / "safety"),
    ]

    # ------------------------------------------------------------------ #
    # INIT                                                                 #
    # ------------------------------------------------------------------ #

    def __init__(self) -> None:
        for path in [self.LOGS_DIR, self.SANDBOX_DIR]:
            path.mkdir(parents=True, exist_ok=True)

        # Resolve AUDIO_INPUT_INDEX: treat -1 as "let OS decide" (None)
        if isinstance(self.AUDIO_INPUT_INDEX, int) and self.AUDIO_INPUT_INDEX < 0:
            self.AUDIO_INPUT_INDEX = None

        # Mutable runtime lists (can be patched by dynamic_settings.json)
        self.RISKY_INTENTS: List[str] = ["file_delete", "shell_command", "run_command"]
        self.COMPLEX_INTENTS: List[str] = ["write_", "debug_", "complex_"]

        self._load_dynamic_settings()

    def _load_dynamic_settings(self) -> None:
        """Merge dynamic intent overrides from JSON without crashing on bad data."""
        if not self.DYNAMIC_SETTINGS_FILE.exists():
            logger.debug("No dynamic_settings.json found — using defaults.")
            return
        try:
            with open(self.DYNAMIC_SETTINGS_FILE) as f:
                data = json.load(f)
            self.RISKY_INTENTS   = data.get("risky_intents",   self.RISKY_INTENTS)
            self.COMPLEX_INTENTS = data.get("complex_intents", self.COMPLEX_INTENTS)
            logger.info("Loaded dynamic intent configuration from JSON.")
        except json.JSONDecodeError:
            logger.error("Corrupted dynamic_settings.json — using defaults.")
        except Exception as exc:
            logger.error("Failed to load dynamic settings: %s — using defaults.", exc)


# Global singleton
settings = Settings()