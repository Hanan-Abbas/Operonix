"""
voice/listener.py — Operonix AI OS Agent
══════════════════════════════════════════
Simpler, single-shot listener used outside the main pipeline
(e.g. interactive CLI testing).  Uses the shared AudioManager and
the unified NoiseFilter (DeepFilterNet / noisereduce).
"""
from __future__ import annotations

import logging
import os
import time
import warnings
from ctypes import CFUNCTYPE, cdll, c_char_p, c_int

# Suppress ALSA / JACK log spam on Linux
os.environ.setdefault("PyTorch_NNPACK_ENABLED", "0")
os.environ.setdefault("TORCH_CPP_LOG_LEVEL", "ERROR")
os.environ.setdefault("JACK_NO_START_SERVER", "1")

_ERROR_HANDLER_FUNC = CFUNCTYPE(None, c_char_p, c_int, c_char_p, c_int, c_char_p)
def _py_error_handler(filename, line, function, err, fmt):
    pass
_c_error_handler = _ERROR_HANDLER_FUNC(_py_error_handler)
try:
    cdll.LoadLibrary("libasound.so.2").snd_lib_error_set_handler(_c_error_handler)
except OSError:
    pass

warnings.filterwarnings("ignore", category=UserWarning)

import numpy as np
import torch
from silero_vad import load_silero_vad

from core.config import settings
from voice.audio_manager import AudioManager
from voice.deep_noise_filter import NoiseFilter
from voice.stt import SpeechToText

logger = logging.getLogger("VoiceListener")

_VAD_FRAME = 512  # samples — Silero requirement at 16 kHz


class VoiceListener:
    """Simple blocking listener: record → denoise → transcribe → return text."""

    def __init__(self, audio_manager: AudioManager) -> None:
        if audio_manager is None:
            raise ValueError("VoiceListener requires an AudioManager instance.")

        logger.info("🎙️ VoiceListener: Loading Silero VAD …")
        torch.set_num_threads(1)
        self.vad_model = load_silero_vad()

        self.audio_manager = audio_manager
        self.rate: int = getattr(audio_manager, "rate", 16000) or 16000
        self.chunk: int = getattr(audio_manager, "chunk", _VAD_FRAME)

        stt_size = getattr(settings, "STT_MODEL_SIZE", "small")
        self.stt = SpeechToText(model_size=stt_size)
        self.noise_filter = NoiseFilter(rate=self.rate)

        self.max_record_seconds: float = float(getattr(settings, "MAX_RECORD_SECONDS", 10))
        self.silence_limit: int = int(getattr(settings, "SILENCE_LIMIT", 80))

        logger.info("VoiceListener ready.")

    # ── Public API ────────────────────────────────────────────────────────────

    def listen_until_silent(self) -> str | None:
        """
        Record audio until silence is detected (or timeout).
        Returns transcribed text, or None if nothing was heard.
        """
        logger.info("\n🎤 Listening for command …")

        voiced_frames: list[bytes] = []
        silent_chunks: int = 0
        triggered: bool = False
        pending: np.ndarray = np.zeros(0, dtype=np.int16)
        start = time.time()

        while time.time() - start < self.max_record_seconds:
            data = self.audio_manager.read_chunk()
            if data is None:
                continue

            chunk_1d = data.flatten().astype(np.int16)
            if chunk_1d.size == 0:
                continue

            pending = np.concatenate([pending, chunk_1d])

            while pending.size >= _VAD_FRAME:
                frame = pending[:_VAD_FRAME].copy()
                pending = pending[_VAD_FRAME:]

                # Always store raw bytes (cleaned after capture)
                voiced_frames.append(frame.tobytes())

                # VAD scoring
                f32 = frame.astype(np.float32) / 32768.0
                tensor = torch.from_numpy(f32).unsqueeze(0)
                try:
                    speech_prob = float(self.vad_model(tensor, self.rate).item())
                except Exception:
                    continue

                if speech_prob > 0.4:
                    if not triggered:
                        logger.info("🔊 Speech detected …")
                    triggered = True
                    silent_chunks = 0
                elif triggered:
                    silent_chunks += 1
                    if silent_chunks > self.silence_limit:
                        logger.info("🔇 Silence — processing …")
                        break

            if triggered and silent_chunks > self.silence_limit:
                break

        if not triggered or not voiced_frames:
            return None

        # ── Process ───────────────────────────────────────────────────────────
        raw_int16 = np.frombuffer(b"".join(voiced_frames), dtype=np.int16)
        audio_f32 = raw_int16.astype(np.float32) / 32768.0

        cleaned = self.noise_filter.reduce_noise(audio_f32)
        text_result = self.stt.transcribe_numpy_array(cleaned, return_metadata=False)

        text = text_result if isinstance(text_result, str) else ""
        if text and len(text.strip()) > 1:
            return text.strip()
        return None