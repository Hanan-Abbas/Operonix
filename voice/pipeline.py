import numpy as np
import torch
import noisereduce as nr
from silero_vad import load_silero_vad
from voice.stt import SpeechToText

class VoicePipeline:
    """🎙️ Refactored: A collection of voice processing tools."""

    def __init__(self, audio_manager):
        print("🎙️ Voice Pipeline: Initializing processing tools...")
        self.audio_manager = audio_manager # Shared hardware source
        self.vad_model = load_silero_vad()
        self.stt = SpeechToText(model_size="base")
        self.rate = 16000

    def capture_command(self):
        """Listens until silence is detected, using the shared audio_manager."""
        print("🎤 Listening for command...")
        self.vad_model.reset_states()
        voiced_frames = []
        silent_chunks = 0
        triggered = False

        # Loop for a max of 10 seconds to prevent hanging
        for _ in range(300): 
            chunk = self.audio_manager.read_chunk()
            if chunk is None: continue

            voiced_frames.append(chunk.copy())
            audio_float32 = chunk.flatten().astype(np.float32) / 32768.0
            audio_tensor = torch.from_numpy(audio_float32).unsqueeze(0)
            speech_prob = self.vad_model(audio_tensor, self.rate).item()

            if speech_prob > 0.55:
                triggered = True
                silent_chunks = 0
            elif triggered:
                silent_chunks += 1
                if silent_chunks > 50: break # Silence detected

        if not triggered or len(voiced_frames) < 10:
            return None

        # Process and Transcribe
        full_audio = np.concatenate(voiced_frames, axis=0).flatten().astype(np.float32) / 32768.0
        cleaned = nr.reduce_noise(y=full_audio, sr=self.rate, stationary=True)
        return self.stt.transcribe_numpy_array(cleaned)