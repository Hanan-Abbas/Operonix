"""
🎙️ Improved Voice Pipeline with better error handling.
Distinguishes between silence and STT errors.

FIX CHANGELOG (Step 2):
  • _stop_requested threading.Event added. capture_command() checks it
    inside the capture loop. When set, the loop exits immediately but
    still processes whatever voiced_frames were already collected — the
    last spoken command is transcribed and returned rather than dropped.
  • request_stop() sets _stop_requested so mode_manager._teardown_voice()
    can signal the pipeline to finish gracefully before audio_manager.stop()
    closes the hardware stream.
  • flush_tail() drains the hardware buffer via audio_manager.drain_tail()
    and runs a final VAD pass to catch any trailing speech. Called by
    teardown after request_stop() and before audio_manager.stop().
  • reset() clears _stop_requested so a fresh capture_command() works
    after voice mode is re-activated.
"""

from __future__ import annotations

import asyncio
import logging
import threading
from typing import Optional
import os
import time

os.environ.setdefault("PyTorch_NNPACK_ENABLED", "0")
os.environ.setdefault("TORCH_CPP_LOG_LEVEL", "ERROR")
os.environ.setdefault("JACK_NO_START_SERVER", "1")

import numpy as np
import torch
from silero_vad import load_silero_vad

from core.config import settings
from core.event_bus import bus
from voice.audio_manager import AudioManager
from voice.deep_noise_filter import NoiseFilter
from voice.stt import SpeechToText

logger = logging.getLogger("VoicePipeline")

_VAD_FRAME = 512  # Silero-VAD requirement at 16 kHz

# How long to wait (seconds) for capture_command() to acknowledge
# request_stop() before teardown gives up and forces audio_manager.stop().
_FLUSH_WAIT_S: float = 2.0


class VoicePipeline:
    """✅ Improved voice capture pipeline with comprehensive error handling."""

    def __init__(self, audio_manager: AudioManager) -> None:
        logger.info("🎙️ VoicePipeline: Initialising …")

        if audio_manager is None:
            raise ValueError("VoicePipeline requires an AudioManager instance")

        self.audio_manager = audio_manager

        # Stop/flush coordination — set by request_stop(), checked in capture_command()
        self._stop_requested = threading.Event()
        # Set when capture_command() has fully exited after a stop request.
        self._stopped = threading.Event()
        self._stopped.set()   # starts in "stopped" state (not currently capturing)

        torch.set_num_threads(1)
        self.vad_model = load_silero_vad()

        stt_size = getattr(settings, "STT_MODEL_SIZE", "small")
        self.stt = SpeechToText(model_size=stt_size)
        self.noise_filter = NoiseFilter(rate=16000)

        self.rate: int = 16000
        self.speech_threshold: float = float(getattr(settings, "VOICE_VAD_SPEECH_THRESHOLD", 0.45))
        self.max_chunks: int = int(getattr(settings, "VOICE_CAPTURE_MAX_CHUNKS", 300))
        self.silence_chunks: int = int(getattr(settings, "VOICE_CAPTURE_SILENCE_CHUNKS", 50))
        self.pre_roll_chunks: int = int(getattr(settings, "VOICE_CAPTURE_PREROLL_CHUNKS", 8))

        logger.info(
            "VoicePipeline ready — VAD threshold=%.2f, silence=%d frames, max=%d frames",
            self.speech_threshold, self.silence_chunks, self.max_chunks,
        )

    # ── Stop/flush control ────────────────────────────────────────────────────

    def request_stop(self) -> None:
        """
        Signal the active capture_command() loop to stop gracefully.

        The loop will exit on the next VAD frame check, then process
        whatever voiced_frames it already collected before returning.
        This ensures the last spoken word is not cut off.

        After calling this, callers should wait for _stopped to be set
        (or call flush_tail() which does this) before stopping the
        AudioManager hardware stream.
        """
        logger.info("VoicePipeline: stop requested — will finish current utterance.")
        self._stop_requested.set()

    def flush_tail(self, timeout: float = _FLUSH_WAIT_S) -> None:
        """
        Wait for the current capture_command() to finish processing the
        tail of the utterance, then drain any residual hardware buffer.

        Called by mode_manager._teardown_voice() after request_stop()
        and before audio_manager.stop().

        Steps:
          1. Wait up to `timeout` seconds for _stopped to be set (i.e. for
             capture_command() to return naturally after seeing _stop_requested).
          2. Drain up to 10 chunks of residual hardware buffer via
             audio_manager.drain_tail() so no audio is left in the OS buffer.
          3. Clears _stop_requested so a future capture_command() works normally.
        """
        waited = self._stopped.wait(timeout=timeout)
        if not waited:
            logger.warning(
                "VoicePipeline: flush_tail timed out after %.1fs — "
                "capture_command() did not exit cleanly. Proceeding with teardown.", timeout
            )

        # Drain whatever is left in the hardware buffer.
        tail = self.audio_manager.drain_tail(num_chunks=10)
        if tail is not None and tail.size > 0:
            logger.debug(
                "VoicePipeline: drained %d tail samples from hardware buffer.", tail.size
            )

        # Reset for next voice activation.
        self._stop_requested.clear()
        self._stopped.set()
        logger.info("VoicePipeline: flush complete — ready for next activation.")

    def reset(self) -> None:
        """
        Fully reset pipeline state for a fresh voice activation.
        Call from mode_manager._startup_voice() after audio_manager.start().
        """
        self._stop_requested.clear()
        self._stopped.set()
        self.vad_model.reset_states()
        logger.info("VoicePipeline: reset — ready to capture.")

    # ── Main entry point ──────────────────────────────────────────────────────

    def capture_command(self) -> Optional[dict]:
        """
        Capture voice command with comprehensive error handling.

        FIX: Marks _stopped=False on entry and _stopped=True on exit.
             Checks _stop_requested inside the capture loop — when set,
             exits the loop immediately but still transcribes any
             voiced_frames already collected so the last word is returned.
        """
        logger.info("🎤 Listening for command …")

        # Signal that a capture is now in progress.
        self._stopped.clear()

        start_time = time.time()

        text = ""
        meta = {}
        confidence = 0.0
        final_text = ""
        final_meta = {}
        provider = "local"

        # Discard stale audio
        clear_n = int(getattr(settings, "VOICE_CLEAR_BUFFER_CHUNKS", 1))
        if clear_n > 0:
            self.audio_manager.clear_buffer(num_chunks=clear_n)

        self.vad_model.reset_states()

        voiced_frames: list[np.ndarray] = []
        pre_roll: list[np.ndarray] = []
        silent_frames: int = 0
        triggered: bool = False
        pending: np.ndarray = np.zeros(0, dtype=np.int16)
        frames_processed: int = 0

        # ── Capture loop ──────────────────────────────────────────────────────
        while frames_processed < self.max_chunks:

            # FIX: Check stop flag at the top of every iteration.
            # Exit the loop but keep whatever voiced_frames we have so
            # the utterance is still transcribed — not silently dropped.
            if self._stop_requested.is_set():
                logger.info(
                    "VoicePipeline: stop requested mid-capture — "
                    "flushing %d voiced frames.", len(voiced_frames)
                )
                break

            chunk = self.audio_manager.read_chunk()
            if chunk is None:
                time.sleep(0.01)
                continue

            chunk_1d = chunk.flatten().astype(np.int16)
            if chunk_1d.size == 0:
                continue

            pending = np.concatenate([pending, chunk_1d])

            # Process complete VAD frames
            while pending.size >= _VAD_FRAME and frames_processed < self.max_chunks:
                frame = pending[:_VAD_FRAME].copy()
                pending = pending[_VAD_FRAME:]
                frames_processed += 1

                speech_prob = self._vad_score(frame)

                if speech_prob > self.speech_threshold:
                    if not triggered:
                        voiced_frames.extend(pre_roll)
                        pre_roll.clear()
                        logger.info("🔊 Speech detected (prob=%.3f)", speech_prob)
                        bus.publish(
                            "speech_detected",
                            {"probability": speech_prob},
                            source="pipeline"
                        )
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
                    pre_roll.append(frame)
                    if len(pre_roll) > self.pre_roll_chunks:
                        pre_roll.pop(0)

            if triggered and silent_frames > self.silence_chunks:
                break

        # Signal that capture_command() is done — flush_tail() waits for this.
        self._stopped.set()

        # ── Validate capture ──────────────────────────────────────────────────
        duration = time.time() - start_time

        if not triggered or len(voiced_frames) < 10:
            logger.info("🔇 No speech detected (duration=%.1fs)", duration)
            bus.publish(
                "silence_detected",
                {
                    "duration": duration,
                    "frames_processed": frames_processed,
                    "reason": "no_speech_above_threshold"
                },
                source="pipeline"
            )
            return None

        # ── Denoise ───────────────────────────────────────────────────────────
        full_audio = np.concatenate(voiced_frames).astype(np.float32) / 32768.0

        noise_profile: Optional[np.ndarray] = None
        if pre_roll:
            noise_profile = np.concatenate(pre_roll).astype(np.float32) / 32768.0

        cleaned = self.noise_filter.reduce_noise(full_audio, noise_profile=noise_profile)
        cleaned = np.clip(cleaned, -1.0, 1.0).astype(np.float32)

        logger.info("🎙️ Audio captured: %.1fs, %d frames, %.1f dB RMS",
                    duration, len(voiced_frames), self._estimate_rms_db(cleaned))

        # ── Transcribe ────────────────────────────────────────────────────────
        try:
            result = self.stt.transcribe_numpy_array(cleaned, return_metadata=True)

            if isinstance(result, tuple):
                text, meta = result
            else:
                text, meta = result, {}

            confidence = self.stt.estimate_confidence_with_text(text, meta)
        except Exception as e:
            logger.error("❌ Local STT failed: %s", e)
            text = ""

        if not text:
            logger.warning(
                "🔇 STT returned empty (confidence=%.2f, duration=%.1fs)",
                confidence, duration
            )

            if confidence < 0.15:
                bus.publish(
                    "silence_detected",
                    {
                        "duration": duration,
                        "confidence": confidence,
                        "reason": "low_confidence_after_stt"
                    },
                    source="pipeline"
                )
            else:
                bus.publish(
                    "stt_error",
                    {
                        "confidence": confidence,
                        "duration": duration,
                        "meta": meta,
                        "reason": "empty_transcription"
                    },
                    source="pipeline"
                )
            return None

        # ── Cloud fallback with hybrid mode ───────────────────────────────────
        try:
            from voice.cloud_stt import transcribe_audio_hybrid

            final_text, final_meta, provider = transcribe_audio_hybrid(
                audio_float32=cleaned,
                sample_rate=self.rate,
                local_text=text,
                local_meta=meta,
                local_confidence=confidence,
            )
        except Exception as e:
            logger.warning("Cloud fallback failed: %s. Using local result.", e)
            final_text = text
            final_meta = meta
            provider = "local"

        final_meta = dict(final_meta or {})
        final_meta["confidence"] = confidence
        final_meta["frames_captured"] = len(voiced_frames)
        final_meta["duration"] = duration

        if confidence < getattr(settings, "STT_MIN_CONFIDENCE", 0.35):
            logger.warning(
                "⚠️ Low confidence (%.2f). Result may be inaccurate: '%s'",
                confidence, final_text
            )
            bus.publish(
                "low_confidence_warning",
                {
                    "text": final_text,
                    "confidence": confidence,
                    "duration": duration,
                    "provider": provider
                },
                source="pipeline"
            )

        logger.info(
            "✅ Transcribed [%s, conf=%.2f, dur=%.1fs]: '%s'",
            provider, confidence, duration, final_text
        )

        bus.publish(
            "transcription_complete",
            {
                "text": final_text,
                "confidence": confidence,
                "provider": provider,
                "duration": duration
            },
            source="pipeline"
        )

        return {
            "text": final_text,
            "stt": final_meta,
            "provider": provider,
            "confidence": confidence,
            "duration_seconds": duration
        }

    # ── Helpers ───────────────────────────────────────────────────────────────

    def _vad_score(self, frame_int16: np.ndarray) -> float:
        """Get speech probability from Silero VAD."""
        try:
            f32 = frame_int16.astype(np.float32) / 32768.0
            tensor = torch.from_numpy(f32).unsqueeze(0)
            return float(self.vad_model(tensor, self.rate).item())
        except Exception as exc:
            logger.debug("VAD error (ignored): %s", exc)
            return 0.0

    def _estimate_rms_db(self, audio: np.ndarray) -> float:
        """Estimate RMS level in dB."""
        try:
            rms = float(np.sqrt(np.mean(audio.astype(np.float64) ** 2)))
            if rms < 1e-8:
                return -100.0
            return 20.0 * np.log10(rms)
        except Exception:
            return -100.0