import numpy as np
import torch
import noisereduce as nr
from silero_vad import load_silero_vad
from voice.stt import SpeechToText
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
        voiced_frames = []
        pre_roll = []
        silent_chunks = 0
        triggered = False

        # Loop for a max of 10 seconds to prevent hanging
        for _ in range(self.max_chunks):
            chunk = self.audio_manager.read_chunk()
            if chunk is None: continue

            audio_float32 = chunk.flatten().astype(np.float32) / 32768.0
            if len(audio_float32) != 512:
                continue

            audio_tensor = torch.from_numpy(audio_float32).unsqueeze(0)
            
            try:
                speech_prob = self.vad_model(audio_tensor, self.rate).item()
            except Exception:
                continue

            if speech_prob > self.speech_threshold:
                if not triggered:
                    voiced_frames.extend(pre_roll)
                triggered = True
                silent_chunks = 0
                voiced_frames.append(chunk.copy())
            elif triggered:
                silent_chunks += 1
                voiced_frames.append(chunk.copy())
                if silent_chunks > self.silence_chunks:
                    break # Silence detected
            else:
                pre_roll.append(chunk.copy())
                if len(pre_roll) > self.pre_roll_chunks:
                    pre_roll.pop(0)

        if not triggered or len(voiced_frames) < 10:
            return None

        # Process and Transcribe
        full_audio = np.concatenate(voiced_frames, axis=0).flatten().astype(np.float32) / 32768.0
        # Keep denoise conservative to avoid harming consonants.
        cleaned = nr.reduce_noise(
            y=full_audio,
            sr=self.rate,
            stationary=False,
            prop_decrease=0.5,
        )
        cleaned = np.clip(cleaned, -1.0, 1.0).astype(np.float32)
        return self.stt.transcribe_numpy_array(cleaned)