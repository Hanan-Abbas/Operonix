"""
voice/stt.py — Operonix AI OS Agent
═════════════════════════════════════
Faster-Whisper speech-to-text with:
  • Auto device selection (CUDA when available, CPU otherwise)
  • Per-device compute_type override via STT_COMPUTE_TYPE
  • Configurable normalisation target level
  • Confidence scoring from model metadata
  • No stray PyAudio instance (listen_and_transcribe uses AudioManager)
"""
from __future__ import annotations

import logging
import os
import re
from typing import Optional, Tuple, Union

import numpy as np

from core.config import settings

# Suppress irrelevant low-level log spam
os.environ.setdefault("PyTorch_NNPACK_ENABLED", "0")
os.environ.setdefault("JACK_NO_START_SERVER", "1")

logger = logging.getLogger("STT")


# ── Audio helpers ─────────────────────────────────────────────────────────────

def _normalize_audio(audio: np.ndarray) -> np.ndarray:
    """Normalise float32 audio to a target RMS dB level."""
    if not bool(getattr(settings, "STT_NORMALIZE_AUDIO", True)):
        return audio

    target_db: float = float(getattr(settings, "STT_NORMALIZE_TARGET_DB", -20.0))
    rms = float(np.sqrt(np.mean(audio.astype(np.float64) ** 2)))
    if rms < 1e-8:
        return audio  # silence — nothing to normalise

    current_db = 20.0 * np.log10(rms)
    gain = 10.0 ** ((target_db - current_db) / 20.0)
    return np.clip(audio * gain, -1.0, 1.0).astype(np.float32)


def _resolve_compute_device() -> Tuple[str, str]:
    """
    Return (device, compute_type) based on settings and hardware availability.

    STT_DEVICE = "auto"  → cuda if torch sees a GPU, otherwise cpu
    STT_DEVICE = "cuda"  → forced cuda
    STT_DEVICE = "cpu"   → forced cpu
    """
    device_cfg = getattr(settings, "STT_DEVICE", "auto").lower().strip()
    compute_cfg = getattr(settings, "STT_COMPUTE_TYPE", "int8").lower().strip()

    if device_cfg == "auto":
        try:
            import torch
            device = "cuda" if torch.cuda.is_available() else "cpu"
        except ImportError:
            device = "cpu"
    else:
        device = device_cfg

    # On CPU, float16 is unsupported by most runtimes — coerce to int8
    if device == "cpu" and compute_cfg == "float16":
        logger.info("STT: float16 not supported on CPU — using int8.")
        compute_cfg = "int8"

    return device, compute_cfg


# ── SpeechToText ──────────────────────────────────────────────────────────────

class SpeechToText:
    """Faster-Whisper wrapper with confidence estimation."""

    def __init__(self, model_size: Optional[str] = None) -> None:
        if model_size is None:
            model_size = getattr(settings, "STT_MODEL_SIZE", "base")

        device, compute_type = _resolve_compute_device()
        logger.info(
            "🎙️ STT: Loading Faster-Whisper '%s' on %s (%s) …",
            model_size, device, compute_type,
        )

        from faster_whisper import WhisperModel
        self.model = WhisperModel(model_size, device=device, compute_type=compute_type)
        logger.info("🎙️ STT: Model ready.")

        self.rate: int = 16000  # Whisper always expects 16 kHz

        # Decoding parameters — all configurable via env / config
        self.beam_size: int = int(getattr(settings, "STT_BEAM_SIZE", 5))
        self.best_of: int = int(getattr(settings, "STT_BEST_OF", 5))
        self.temperature: float = float(getattr(settings, "STT_TEMPERATURE", 0.0))
        self.language: str = getattr(settings, "STT_LANGUAGE", "en")

    # ── Transcription ─────────────────────────────────────────────────────────

    def transcribe_raw_bytes(
        self,
        audio_data: bytes,
        return_metadata: bool = False,
    ) -> Union[str, Tuple[str, dict]]:
        """Accept raw 16-bit PCM bytes and return transcribed text."""
        if not audio_data:
            return ("", {}) if return_metadata else ""
        audio_np = np.frombuffer(audio_data, dtype=np.int16).astype(np.float32) / 32768.0
        return self._transcribe_audio(audio_np, return_metadata=return_metadata)

    def transcribe_numpy_array(self, audio_np, return_metadata=False):
        lang = getattr(settings, "STT_LANGUAGE", "en")
        # Pure signal processing - no I/O
        audio_np = _normalize_audio(audio_np)
        segments, info = self.model.transcribe(audio_np, language=lang)
        return text, metadata

    # ── Core inference ────────────────────────────────────────────────────────

    def _transcribe_audio(
        self,
        audio_np: np.ndarray,
        return_metadata: bool = False,
    ) -> Union[str, Tuple[str, dict]]:
        audio_np = _normalize_audio(audio_np)
        initial_prompt = self._build_initial_prompt()

        try:
            segments_iter, info = self.model.transcribe(
                audio_np,
                beam_size=self.beam_size,
                best_of=self.best_of,
                temperature=self.temperature,
                language=self.language,
                condition_on_previous_text=False,
                vad_filter=True,
                initial_prompt=initial_prompt,
            )
            segments = list(segments_iter)
        except Exception as exc:
            logger.error("STT transcription failed: %s", exc)
            return ("", {}) if return_metadata else ""

        text = " ".join(s.text.strip() for s in segments if s.text).strip()

        if not return_metadata:
            return text

        avg_logprobs = [
            float(s.avg_logprob)
            for s in segments
            if isinstance(getattr(s, "avg_logprob", None), (int, float))
        ]
        no_speech_probs = [
            float(s.no_speech_prob)
            for s in segments
            if isinstance(getattr(s, "no_speech_prob", None), (int, float))
        ]

        meta = {
            "language": getattr(info, "language", None),
            "duration": getattr(info, "duration", None),
            "mean_avg_logprob": (sum(avg_logprobs) / len(avg_logprobs)) if avg_logprobs else None,
            "mean_no_speech_prob": (sum(no_speech_probs) / len(no_speech_probs)) if no_speech_probs else None,
            "segments": len(segments),
        }
        return text, meta

    # ── Confidence estimation ─────────────────────────────────────────────────

    def estimate_confidence(self, meta: dict) -> float:
        """Produce a 0–1 confidence score from Whisper metadata."""
        if not isinstance(meta, dict):
            return 0.0

        segments = meta.get("segments")
        if not isinstance(segments, int) or segments <= 0:
            return 0.0

        lp = meta.get("mean_avg_logprob")
        lp_score = max(0.0, min(1.0, (float(lp) + 2.0) / 1.8)) if isinstance(lp, (int, float)) else 0.4

        ns = meta.get("mean_no_speech_prob")
        ns_score = 1.0 - max(0.0, min(1.0, float(ns))) if isinstance(ns, (int, float)) else 0.6

        return max(0.0, min(1.0, 0.65 * lp_score + 0.35 * ns_score))

    def estimate_confidence_with_text(self, text: str, meta: dict) -> float:
        """Blend acoustic confidence with transcript information-content."""
        base = self.estimate_confidence(meta)
        t = (text or "").strip().lower()
        if not t:
            return 0.0

        tokens = re.findall(r"[a-z0-9]+", t)
        if not tokens:
            return max(0.0, base - 0.25)

        unique_ratio = len(set(tokens)) / float(len(tokens))

        repetition_penalty = (0.4 - unique_ratio) * 0.9 if unique_ratio < 0.4 and len(tokens) >= 4 else 0.0
        short_penalty = 0.25 if len(t) < 6 else 0.0

        vocab_penalty = 0.0
        try:
            from capabilities.registry import capability_registry
            intents = capability_registry.get_all_names()
            vocab = {w.lower() for intent in intents for w in intent.replace("_", " ").split() if w}
            if vocab and not any(tok in vocab for tok in tokens):
                vocab_penalty = 0.25
        except Exception:
            pass

        return max(0.0, min(1.0, base - repetition_penalty - short_penalty - vocab_penalty))

    # ── Prompt builder ────────────────────────────────────────────────────────

    def _build_initial_prompt(self) -> Optional[str]:
        if not bool(getattr(settings, "STT_USE_INITIAL_PROMPT", True)):
            return None

        mode = getattr(settings, "STT_INITIAL_PROMPT_MODE", "capabilities")
        max_words = int(getattr(settings, "STT_INITIAL_PROMPT_MAX_WORDS", 40))

        if mode == "minimal":
            return "Transcribe the user's spoken command in plain English. Do not invent extra words."

        core = [
            "create", "delete", "file", "folder", "directory", "named",
            "write", "read", "move", "copy", "list", "show", "open", "close",
            "make", "remove", "new", "save", "edit", "search", "find", "run",
            "execute", "command", "script", "install", "git", "commit", "push",
        ]
        try:
            from capabilities.registry import capability_registry
            for intent in capability_registry.get_all_names()[:64]:
                core.extend(intent.replace("_", " ").split())
        except Exception:
            pass

        seen: set = set()
        unique: list = []
        for w in core:
            w = w.strip().lower()
            if w and w not in seen:
                seen.add(w)
                unique.append(w)

        return "Common commands: " + ", ".join(unique[:max_words])


# ── Standalone smoke-test ─────────────────────────────────────────────────────
if __name__ == "__main__":
    import sounddevice as sd
    import queue

    logging.basicConfig(level=logging.INFO)
    stt = SpeechToText()
    q: queue.Queue = queue.Queue()

    def _cb(indata, frames, time_info, status):
        q.put(indata.copy())

    DURATION = 4  # seconds
    RATE = 16000
    CHUNK = 1024

    print(f"\n🎤 Recording {DURATION}s …")
    with sd.InputStream(samplerate=RATE, channels=1, dtype="int16", blocksize=CHUNK, callback=_cb):
        frames = [q.get() for _ in range(int(RATE / CHUNK * DURATION))]

    audio = np.concatenate(frames, axis=0).flatten().astype(np.float32) / 32768.0
    text = stt.transcribe_numpy_array(audio)
    print(f"🗣️  You said: {text or '(nothing detected)'}")