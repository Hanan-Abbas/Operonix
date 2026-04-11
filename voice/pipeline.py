# voice/pipeline_improved.py
"""
🎙️ Improved Voice Pipeline with better error handling.
Distinguishes between silence and STT errors.
"""

from __future__ import annotations

import asyncio
import logging
from typing import Optional

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


class VoicePipelineImproved:
    """✅ Improved voice capture pipeline with comprehensive error handling."""

    def __init__(self, audio_manager: AudioManager) -> None:
        logger.info("🎙️ VoicePipeline: Initialising …")
        
        if audio_manager is None:
            raise ValueError("VoicePipeline requires an AudioManager instance")
        
        self.audio_manager = audio_manager

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

    # ── Main entry point ──────────────────────────────────────────────────────

    def capture_command(self) -> Optional[dict]:
        """
        Capture voice command with comprehensive error handling.
        
        Returns:
            {
                "text": str,              # Transcribed text
                "stt": dict,              # STT metadata
                "provider": str,          # "local", "openai", etc
                "confidence": float,      # Confidence score 0-1
                "duration_seconds": float # Recording duration
            }
            or None if capture failed
        """
        logger.info("🎤 Listening for command …")
        
        import time
        start_time = time.time()

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
            chunk = self.audio_manager.read_chunk()
            if chunk is None:
                await asyncio.sleep(0.01)  # Prevent busy-waiting
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
                        await bus.emit(
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
                    # Pre-speech: rolling window for noise profile
                    pre_roll.append(frame)
                    if len(pre_roll) > self.pre_roll_chunks:
                        pre_roll.pop(0)

            if triggered and silent_frames > self.silence_chunks:
                break

        # ── Validate capture ──────────────────────────────────────────────────
        duration = time.time() - start_time
        
        if not triggered or len(voiced_frames) < 10:
            logger.info("🔇 No speech detected (duration=%.1fs)", duration)
            
            await bus.emit(
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
        result = self.stt.transcribe_numpy_array(cleaned, return_metadata=True)
        
        if isinstance(result, tuple):
            text, meta = result
        else:
            text, meta = result, {}

        confidence = self.stt.estimate_confidence_with_text(text, meta)

        # ✅ IMPROVED: Distinguish between silence and STT error
        if not text:
            logger.warning(
                "🔇 STT returned empty (confidence=%.2f, duration=%.1fs)",
                confidence, duration
            )
            
            if confidence < 0.15:
                # Likely just silence/noise
                await bus.emit(
                    "silence_detected",
                    {
                        "duration": duration,
                        "confidence": confidence,
                        "reason": "low_confidence_after_stt"
                    },
                    source="pipeline"
                )
            else:
                # Confidence decent but no text = STT failure
                await bus.emit(
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

        # ✅ IMPROVED: Cloud fallback with hybrid mode
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

        # ✅ IMPROVED: Log confidence warnings
        if confidence < getattr(settings, "STT_MIN_CONFIDENCE", 0.35):
            logger.warning(
                "⚠️ Low confidence (%.2f). Result may be inaccurate: '%s'",
                confidence, final_text
            )
            
            await bus.emit(
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

        await bus.emit(
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
                return -np.inf
            return 20.0 * np.log10(rms)
        except Exception:
            return 0.0


# ============================================================================
# USAGE IN core/orchestrator.py
# ============================================================================
"""
from voice.pipeline_improved import VoicePipelineImproved

class Orchestrator:
    async def handle_wake_word(self, event) -> None:
        trigger = event.data.get("trigger", "?")
        score = event.data.get("score", 0.0)
        logger.info("🔔 Wake word detected (score=%.2f) — capturing command", score)

        loop = asyncio.get_running_loop()
        
        try:
            command = await loop.run_in_executor(
                None, 
                self.pipeline.capture_command
            )

            if command and command.get("text"):
                text = command["text"]
                confidence = command.get("confidence", 0.0)
                
                logger.info("🎤 Command (conf=%.2f): '%s'", confidence, text)
                
                await bus.emit(
                    "user_input_received",
                    {
                        "text": text,
                        "stt": command.get("stt", {}),
                        "stt_provider": command.get("provider"),
                        "confidence": confidence,
                        "duration": command.get("duration_seconds", 0)
                    },
                    source="orchestrator",
                )
            else:
                logger.info("🔇 Command capture returned None (likely silence)")
                
        except Exception as e:
            logger.error("❌ Voice capture failed: %s", e)
            await bus.emit(
                "voice_capture_error",
                {"error": str(e)},
                source="orchestrator"
            )
"""