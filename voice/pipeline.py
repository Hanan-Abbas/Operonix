import numpy as np
import torch
import noisereduce as nr
from silero_vad import load_silero_vad
from voice.stt import SpeechToText
from voice.cloud_stt import transcribe_audio_hybrid
from core.config import settings

class VoicePipeline:
    """🎙️ Refactored: A collection of voice processing tools."""

    def __init__(self, audio_manager):
        print("🎙️ Voice Pipeline: Initializing processing tools...")
        self.audio_manager = audio_manager # Shared hardware source
        self.vad_model = load_silero_vad()
        stt_size = getattr(settings, "STT_MODEL_SIZE", "base")
        self.stt = SpeechToText(model_size=stt_size)
        self.rate = 16000
        self.speech_threshold = float(getattr(settings, "VOICE_VAD_SPEECH_THRESHOLD", 0.5))
        self.max_chunks = int(getattr(settings, "VOICE_CAPTURE_MAX_CHUNKS", 300))
        self.silence_chunks = int(getattr(settings, "VOICE_CAPTURE_SILENCE_CHUNKS", 50))
        self.pre_roll_chunks = int(getattr(settings, "VOICE_CAPTURE_PREROLL_CHUNKS", 8))

    def capture_command(self):
        """Listens until silence is detected, using the shared audio_manager."""
        print("🎤 Listening for command...")
        # Flush stale buffered audio so STT starts close to user's command.
        self.audio_manager.clear_buffer(num_chunks=3)
        self.vad_model.reset_states()
        voiced_frames = []  # list[np.ndarray[int16]] of shape (512,)
        pre_roll = []  # list[np.ndarray[int16]] of shape (512,)
        silent_frames = 0
        triggered = False
        pending = np.zeros((0,), dtype=np.int16)

        # We operate VAD on 512-sample frames at 16kHz (Silero expectation).
        # AudioManager may return any chunk size; we re-frame it here.
        frames_processed = 0
        while frames_processed < self.max_chunks:
            chunk = self.audio_manager.read_chunk()
            if chunk is None:
                continue

            chunk_1d = chunk.flatten().astype(np.int16, copy=False)
            if chunk_1d.size == 0:
                continue

            pending = np.concatenate([pending, chunk_1d])
            while pending.size >= 512 and frames_processed < self.max_chunks:
                frame = pending[:512]
                pending = pending[512:]
                frames_processed += 1

                audio_float32 = frame.astype(np.float32) / 32768.0
                audio_tensor = torch.from_numpy(audio_float32).unsqueeze(0)

                try:
                    speech_prob = self.vad_model(audio_tensor, self.rate).item()
                except Exception:
                    continue

                if speech_prob > self.speech_threshold:
                    if not triggered:
                        voiced_frames.extend(pre_roll)
                    triggered = True
                    silent_frames = 0
                    voiced_frames.append(frame.copy())
                elif triggered:
                    silent_frames += 1
                    voiced_frames.append(frame.copy())
                    if silent_frames > self.silence_chunks:
                        break
                else:
                    pre_roll.append(frame.copy())
                    if len(pre_roll) > self.pre_roll_chunks:
                        pre_roll.pop(0)

            if triggered and silent_frames > self.silence_chunks:
                break

        if not triggered or len(voiced_frames) < 10:
            return None

        # Process and Transcribe
        full_audio = np.concatenate(voiced_frames, axis=0).astype(np.float32) / 32768.0
        # Keep denoise conservative to avoid harming consonants.
        cleaned = nr.reduce_noise(
            y=full_audio,
            sr=self.rate,
            stationary=False,
            prop_decrease=0.5,
        )
        cleaned = np.clip(cleaned, -1.0, 1.0).astype(np.float32)
        text, meta = self.stt.transcribe_numpy_array(cleaned, return_metadata=True)
        if not text:
            return None
        confidence = self.stt.estimate_confidence(meta)
        final_text, final_meta, provider = transcribe_audio_hybrid(
            audio_float32=cleaned,
            sample_rate=self.rate,
            local_text=text,
            local_meta=meta,
            local_confidence=confidence,
        )
        final_meta = dict(final_meta or {})
        final_meta["confidence"] = confidence
        return {"text": final_text, "stt": final_meta, "provider": provider}