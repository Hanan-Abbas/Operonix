"""
🛡️ Type-safe configuration validation using Pydantic.
Prevents runtime crashes from bad environment variables.
"""

from typing import Optional, List
from pydantic import Field, validator
from pydantic_settings import BaseSettings
import logging

logger = logging.getLogger("ConfigValidator")


class AudioSettings(BaseSettings):
    """✅ Type-validated audio configuration."""
    
    input_index: Optional[int] = Field(
        default=None, 
        description="Audio device index. None = system default"
    )
    
    @validator("input_index", pre=True)
    def coerce_empty_to_none(cls, v):
        """Convert empty string from .env to None (uses system default mic)."""
        if v == "" or v is None:
            return None
        return v

    rate: int = Field(
        default=16000, 
        ge=8000, 
        le=48000,
        description="Sample rate in Hz (must match STT model)"
    )
    chunk: int = Field(
        default=512,
        ge=256,
        le=8192,
        description="Buffer size in samples"
    )
    volume_boost: float = Field(
        default=1.0,
        ge=0.5,
        le=5.0,
        description="Microphone gain multiplier"
    )
    
    class Config:
        env_prefix = "AUDIO_"
        case_sensitive = False

class SpeechToTextSettings(BaseSettings):
    """✅ Type-validated STT configuration."""
    
    model_size: str = Field(
        default="small",
        pattern="^(tiny|base|small|medium|large-v2|large-v3)$",
        description="Whisper model size"
    )
    device: str = Field(
        default="auto",
        pattern="^(auto|cpu|cuda)$",
        description="Compute device"
    )
    compute_type: str = Field(
        default="int8",
        pattern="^(int8|int16|float16|float32)$",
        description="Quantization type"
    )
    beam_size: int = Field(
        default=5,
        ge=1,
        le=100,
        description="Beam search width"
    )
    best_of: int = Field(
        default=5,
        ge=1,
        le=100,
        description="Best-of sampling"
    )
    temperature: float = Field(
        default=0.0,
        ge=0.0,
        le=1.0,
        description="Sampling temperature"
    )
    language: str = Field(
        default="en",
        min_length=2,
        max_length=5,
        description="ISO language code"
    )
    normalize_audio: bool = Field(
        default=True,
        description="Apply RMS normalization"
    )
    normalize_target_db: float = Field(
        default=-20.0,
        ge=-100.0,
        le=0.0,
        description="Target dB level for normalization"
    )
    use_initial_prompt: bool = Field(
        default=True,
        description="Use vocabulary prompting"
    )
    initial_prompt_max_words: int = Field(
        default=40,
        ge=1,
        le=200,
        description="Max vocabulary words in prompt"
    )
    
    class Config:
        env_prefix = "STT_"
        case_sensitive = False


class VADSettings(BaseSettings):
    """✅ Type-validated Voice Activity Detection configuration."""
    
    speech_threshold: float = Field(
        default=0.45,
        ge=0.0,
        le=1.0,
        description="Speech probability threshold (0-1)"
    )
    max_chunks: int = Field(
        default=300,
        ge=50,
        le=1000,
        description="Max 512-sample frames to capture"
    )
    silence_chunks: int = Field(
        default=50,
        ge=1,
        le=500,
        description="Consecutive silent frames to trigger end"
    )
    preroll_chunks: int = Field(
        default=8,
        ge=0,
        le=50,
        description="Pre-speech frames to include"
    )
    clear_buffer_chunks: int = Field(
        default=1,
        ge=0,
        le=10,
        description="Buffer flush after wake word"
    )
    
    class Config:
        env_prefix = "VOICE_"
        case_sensitive = False


class NoiseFilterSettings(BaseSettings):
    """✅ Type-validated noise reduction configuration."""
    
    enabled: bool = Field(
        default=True,
        description="Enable noise filtering"
    )
    backend: str = Field(
        default="dfn",
        pattern="^(dfn|noisereduce|none)$",
        description="Noise filter backend"
    )
    prop_decrease: float = Field(
        default=0.65,
        ge=0.0,
        le=1.0,
        description="Spectral gating reduction (noisereduce)"
    )
    n_std: float = Field(
        default=1.5,
        ge=0.5,
        le=5.0,
        description="Noise threshold (noisereduce)"
    )
    dfn_atten_limit_db: float = Field(
        default=80.0,
        ge=0.0,
        le=100.0,
        description="DeepFilterNet attenuation limit"
    )
    dfn_post_filter: bool = Field(
        default=True,
        description="Apply post-filter (DeepFilterNet)"
    )
    
    class Config:
        env_prefix = "VOICE_DENOISE_"
        case_sensitive = False


class LLMSettings(BaseSettings):
    """✅ Type-validated LLM configuration."""
    
    deepseek_api_key: Optional[str] = Field(
        default=None,
        description="DeepSeek API key"
    )
    gemini_api_key: Optional[str] = Field(
        default=None,
        description="Google Gemini API key"
    )
    openai_api_key: Optional[str] = Field(
        default=None,
        description="OpenAI API key"
    )
    provider: str = Field(
        default="local",
        pattern="^(local|deepseek|gemini|openai)$",
        description="Primary LLM provider"
    )
    timeout_seconds: float = Field(
        default=30.0,
        ge=5.0,
        le=300.0,
        description="API timeout"
    )
    
    @validator("deepseek_api_key", "gemini_api_key", "openai_api_key", pre=True)
    def validate_api_keys(cls, v):
        """Ensure API keys are stripped and not empty."""
        if v is None:
            return None
        v = str(v).strip()
        return v if v else None
    
    class Config:
        env_prefix = "LLM_"
        case_sensitive = False


class SystemSettings(BaseSettings):
    """✅ Type-validated system configuration."""
    
    max_retry_attempts: int = Field(
        default=3,
        ge=1,
        le=10,
        description="Max retry attempts for failed steps"
    )
    safe_mode: bool = Field(
        default=True,
        description="Enable safety checks"
    )
    prune_timeout: float = Field(
        default=2.0,
        ge=0.5,
        le=10.0,
        description="Pattern pruning timeout (seconds)"
    )
    api_host: str = Field(
        default="localhost",
        description="API server host"
    )
    api_port: int = Field(
        default=8000,
        ge=1024,
        le=65535,
        description="API server port"
    )
    
    class Config:
        env_prefix = "SYSTEM_"
        case_sensitive = False


class ValidatedConfig:
    """🎯 Central validated configuration manager."""
    
    def __init__(self):
        """Initialize all config sections with validation."""
        self.audio = AudioSettings()
        self.stt = SpeechToTextSettings()
        self.vad = VADSettings()
        self.noise_filter = NoiseFilterSettings()
        self.llm = LLMSettings()
        self.system = SystemSettings()
        
        logger.info("✅ All configuration sections validated and loaded")
        self._log_config_summary()
    
    def _log_config_summary(self):
        """Log a summary of active configuration."""
        logger.info(
            "🎙️  Audio Config: rate=%dHz, chunk=%d, boost=%.1fx",
            self.audio.rate, self.audio.chunk, self.audio.volume_boost
        )
        logger.info(
            "🗣️  STT Config: model=%s, device=%s, beam=%d",
            self.stt.model_size, self.stt.device, self.stt.beam_size
        )
        logger.info(
            "🔊 VAD Config: threshold=%.2f, max=%d frames, silence=%d frames",
            self.vad.speech_threshold, self.vad.max_chunks, self.vad.silence_chunks
        )
        logger.info(
            "🎙️  Noise Filter: %s (%s backend)",
            "ENABLED" if self.noise_filter.enabled else "DISABLED",
            self.noise_filter.backend
        )
        logger.info(
            "🧠 LLM Config: provider=%s, timeout=%.1fs",
            self.llm.provider, self.llm.timeout_seconds
        )
    
    def validate_audio_device(self) -> bool:
        """Verify audio device exists and is accessible."""
        try:
            import sounddevice as sd
            
            if self.audio.input_index is None:
                # Use system default
                info = sd.query_devices(None, "input")
                logger.info("✅ Using system default audio device: %s", info.get("name"))
                return True
            
            # Check specific device
            info = sd.query_devices(self.audio.input_index, "input")
            if not info:
                logger.error("❌ Audio device index %d not found", self.audio.input_index)
                return False
            
            if info.get("max_input_channels", 0) < 1:
                logger.error("❌ Device %d has no input channels", self.audio.input_index)
                return False
            
            logger.info("✅ Audio device verified: %s", info.get("name"))
            return True
            
        except Exception as e:
            logger.error("❌ Failed to verify audio device: %s", e)
            return False

class VoiceSettings(BaseSettings):
    """✅ Type-safe voice configuration with validation."""
    
    stt_model_size: str = Field(default="small", 
                                pattern="^(tiny|base|small|medium|large-v2|large-v3)$")
    stt_beam_size: int = Field(default=5, ge=1, le=100)
    stt_normalize_target_db: float = Field(default=-20.0, ge=-100, le=0)
    wake_threshold: float = Field(default=0.50, ge=0.0, le=1.0)
    vad_speech_threshold: float = Field(default=0.45, ge=0.0, le=1.0)
    
    class Config:
        env_prefix = "VOICE_"
        env_file = ".env"

        
# Global validated config instance
try:
    validated_config = ValidatedConfig()
    logger.info("🚀 Configuration validation PASSED")
except Exception as e:
    logger.critical("💥 Configuration validation FAILED: %s", e)
    raise RuntimeError(f"Invalid configuration: {e}") from e