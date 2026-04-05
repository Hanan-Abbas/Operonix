import os
import io
import wave
import numpy as np
import pyaudio
from faster_whisper import WhisperModel

# Suppress annoying background spam before initializing
os.environ['PyTorch_NNPACK_ENABLED'] = '0'
os.environ['JACK_NO_START_SERVER'] = '1'

class SpeechToText:
    def __init__(self, model_size="tiny"):
        """
        Initializes the local Whisper model.
        Model sizes: 'tiny', 'base', 'small', 'medium'
        'tiny' is the fastest and perfect for short command parsing!
        """
        print(f"🎙️ STT: Loading Faster-Whisper model ({model_size})...")
        
        # Running on CPU. Change device to "cuda" if you have a dedicated Nvidia GPU.
        # compute_type="int8" keeps RAM usage low and inference fast.
        self.model = WhisperModel(model_size, device="cpu", compute_type="int8")
        print("🎙️ STT: Model loaded successfully.")
        
        # Audio recording settings
        self.format = pyaudio.paInt16
        self.channels = 1
        self.rate = 16000
        self.chunk = 1024
        self.audio = pyaudio.PyAudio()

    def listen_and_transcribe(self, duration=5):
        """Records audio from the microphone and returns the transcribed text."""
        print(f"\n🎤 Listening for {duration} seconds...")
        
        stream = self.audio.open(
            format=self.format,
            channels=self.channels,
            rate=self.rate,
            input=True,
            frames_per_buffer=self.chunk
        )
              
        frames = []
        for _ in range(0, int(self.rate / self.chunk * duration)):
            data = stream.read(self.chunk)
            frames.append(data)
            
        print("⌛ Processing audio...")
        stream.stop_stream()
        stream.close()
        
        # Combine bytes
        audio_data = b''.join(frames)
        
        # Transcribe directly using the raw byte handler!
        return self.transcribe_raw_bytes(audio_data)

    def transcribe_raw_bytes(self, audio_data):
        """
        🟢 UPGRADED: Accepts raw audio bytes and transcribes them.
        Skips in-memory WAV creation for pure speed!
        """
        if not audio_data:
            return ""

        # Convert the raw 16-bit PCM bytes directly into a float32 NumPy array
        # This is exactly what faster-whisper expects.
        audio_np = np.frombuffer(audio_data, dtype=np.int16).astype(np.float32) / 32768.0
        
        # 🟢 FIX: Forcing language='en' so it doesn't hallucinate non-English
        segments, _ = self.model.transcribe(
            audio_np, beam_size=1, language="en"
        )
        
        text = "".join([segment.text for segment in segments]).strip()
        return text

    def transcribe_numpy_array(self, audio_np):
        """
        🟢 HIGH-SPEED UPGRADE: Accepts a direct float32 numpy array.
        Zero conversion overhead!
        """
        if audio_np is None or len(audio_np) == 0:
            return ""
            
        # Guarantee it's float32 for Faster-Whisper
        audio_np = audio_np.astype(np.float32)
        
        segments, _ = self.model.transcribe(
            audio_np, beam_size=1, language="en"
        )
        
        text = "".join([segment.text for segment in segments]).strip()
        return text
        
# Simple test execution
if __name__ == "__main__":
    stt = SpeechToText(model_size="tiny")
    try:
        while True:
            text = stt.listen_and_transcribe(duration=4)
            if text:
                print(f"🗣️ You said: {text}")
            else:
                print("🔇 No speech detected.")
    except KeyboardInterrupt:
        print("\n🛑 STT stopped.")