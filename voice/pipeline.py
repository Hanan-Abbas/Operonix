"""
voice/pipeline.py — Operonix AI OS Agent
══════════════════════════════════════════
The post-wake voice capture and transcription pipeline.

Flow:
  1. Flush stale buffer after wake word.
  2. Loop: read audio chunks → re-frame to 512-sample VAD windows.
  3. Silero-VAD detects speech start and end.
  4. On speech end: run DeepFilterNet (or noisereduce) on voiced frames.
  5. Faster-Whisper transcribes cleaned audio.
  6. Optional cloud fallback if confidence < threshold.
  7. Return structured result dict.
"""
from __future__ import annotations

import logging
from typing import Optional

import numpy as np
import torch
from silero_vad import load_silero_vad

from core.config import settings
from voice.audio_manager import AudioManager
from voice.deep_noise_filter import NoiseFilter
from voice.stt import SpeechToText
from voice.cloud_stt import transcribe_audio_hybrid

logger = logging.getLogger("VoicePipeline")

# Silero-VAD requires exactly 512 samples per frame at 16 kHz
_VAD_FRAME = 512


class VoicePipeline:
    """🎙️ Full voice capture → denoise → STT pipeline."""

    def __init__(self, audio_manager: AudioManager) -> None:
        logger.info("🎙️ VoicePipeline: Initialising …")
        self.audio_manager = audio_manager

        torch.set_num_threads(1)
        self.vad_model = load_silero_vad()

        stt_size = getattr(settings, "STT_MODEL_SIZE", "small")
        self.stt = SpeechToText(model_size=stt_size)

        self.rate: int = 16000  # must match VAD expectation
        self.noise_filter = NoiseFilter(rate=self.rate)

        # Configurable thresholds (all come from settings / env)
        self.speech_threshold: float = float(getattr(settings, "VOICE_VAD_SPEECH_THRESHOLD", 0.45))
        self.max_chunks: int = int(getattr(settings, "VOICE_CAPTURE_MAX_CHUNKS", 300))
        self.silence_chunks: int = int(getattr(settings, "VOICE_CAPTURE_SILENCE_CHUNKS", 50))
        self.pre_roll_chunks: int = int(getattr(settings, "VOICE_CAPTURE_PREROLL_CHUNKS", 8))

        logger.info(
            "VoicePipeline ready — VAD threshold=%.2f, silence=%d frames, max=%d frames",
            self.speech_threshold, self.silence_chunks, self.max_chunks,
        )

    # ── Main entry point ──────────────────────────────────────────────────────

    def capture_command(self) -> Optional[dict]:
        """
        Block until a voice command is captured and transcribed.

        Returns a dict:  {"text": str, "stt": dict, "provider": str}
        or None if nothing intelligible was captured.
        """
        logger.info("🎤 Listening for command …")

        # Discard audio that accumulated while wake-word handler was running
        clear_n = int(getattr(settings, "VOICE_CLEAR_BUFFER_CHUNKS", 1))
        if clear_n > 0:
            self.audio_manager.clear_buffer(num_chunks=clear_n)

        self.vad_model.reset_states()

        voiced_frames: list[np.ndarray] = []   # int16 arrays, each 512 samples
        pre_roll: list[np.ndarray] = []         # frames before speech starts
        silent_frames: int = 0
        triggered: bool = False
        pending: np.ndarray = np.zeros(0, dtype=np.int16)
        frames_processed: int = 0

        # ── Capture loop ──────────────────────────────────────────────────────
        while frames_processed < self.max_chunks:
            chunk = self.audio_manager.read_chunk()
            if chunk is None:
                continue

            chunk_1d = chunk.flatten().astype(np.int16)
            if chunk_1d.size == 0:
                continue

            pending = np.concatenate([pending, chunk_1d])

            # Process all complete 512-sample frames from the pending buffer
            while pending.size >= _VAD_FRAME and frames_processed < self.max_chunks:
                frame = pending[:_VAD_FRAME].copy()
                pending = pending[_VAD_FRAME:]
                frames_processed += 1

                speech_prob = self._vad_score(frame)

                if speech_prob > self.speech_threshold:
                    if not triggered:
                        # Include pre-roll so we don't clip the start of a word
                        voiced_frames.extend(pre_roll)
                        pre_roll.clear()
                        logger.info("🔊 Speech detected …")
                    triggered = True
                    silent_frames = 0
                    voiced_frames.append(frame)

                elif triggered:
                    silent_frames += 1
                    voiced_frames.append(frame)
                    if silent_frames > self.silence_chunks:
                        logger.info("🔇 Silence detected — processing.")
                        break

                else:
                    # Pre-speech: keep a rolling window as noise profile source
                    pre_roll.append(frame)
                    if len(pre_roll) > self.pre_roll_chunks:
                        pre_roll.pop(0)

            if triggered and silent_frames > self.silence_chunks:
                break

        # ── Validate capture ──────────────────────────────────────────────────
        if not triggered or len(voiced_frames) < 10:
            logger.info("No speech captured.")
            return None

        # ── Denoise ───────────────────────────────────────────────────────────
        full_audio = np.concatenate(voiced_frames).astype(np.float32) / 32768.0

        noise_profile: Optional[np.ndarray] = None
        if pre_roll:
            noise_profile = np.concatenate(pre_roll).astype(np.float32) / 32768.0

        cleaned = self.noise_filter.reduce_noise(full_audio, noise_profile=noise_profile)
        cleaned = np.clip(cleaned, -1.0, 1.0).astype(np.float32)

        # ── Transcribe ────────────────────────────────────────────────────────
        result = self.stt.transcribe_numpy_array(cleaned, return_metadata=True)
        if isinstance(result, tuple):
            text, meta = result
        else:
            text, meta = result, {}

        if not text:
            logger.info("STT returned empty transcript.")
            return None

        confidence = self.stt.estimate_confidence_with_text(text, meta)

        # ── Optional cloud fallback ───────────────────────────────────────────
        final_text, final_meta, provider = transcribe_audio_hybrid(
            audio_float32=cleaned,
            sample_rate=self.rate,
            local_text=text,
            local_meta=meta,
            local_confidence=confidence,
        )

        final_meta = dict(final_meta or {})
        final_meta["confidence"] = confidence
        final_meta["frames_captured"] = len(voiced_frames)

        logger.info("✅ Transcribed [%s, conf=%.2f]: %r", provider, confidence, final_text)
        return {"text": final_text, "stt": final_meta, "provider": provider}

    # ── Helpers ───────────────────────────────────────────────────────────────

    def _vad_score(self, frame_int16: np.ndarray) -> float:
        """Run Silero-VAD on one 512-sample int16 frame. Returns speech probability."""
        try:
            f32 = frame_int16.astype(np.float32) / 32768.0
            tensor = torch.from_numpy(f32).unsqueeze(0)
            return float(self.vad_model(tensor, self.rate).item())
        except Exception as exc:
            logger.debug("VAD error (ignored): %s", exc)
            return 0.0