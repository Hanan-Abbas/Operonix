import os
import io
import wave
import pyaudio
from faster_whisper import WhisperModel

class SpeechToText:
    def __init__(self, model_size="tiny"):
        """
        Initializes the local Whisper model.
        Model sizes: 'tiny', 'base', 'small', 'medium'
        'tiny' is the fastest and perfect for short command parsing!
        """
        print(f"🎙️ STT: Loading Whisper model ({model_size})...")
        # Running on CPU. Change device to "cuda" if you have a dedicated Nvidia GPU.
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
        
        # Convert audio frames to a format Whisper can read directly in memory
        audio_data = b''.join(frames)
        wav_io = io.BytesIO()
        with wave.open(wav_io, 'wb') as wf:
            wf.setnchannels(self.channels)
            wf.setsampwidth(self.audio.get_sample_size(self.format))
            wf.setframerate(self.rate)
            wf.writeframes(audio_data)
            
        wav_io.seek(0)
        
        # Transcribe the audio
        segments, info = self.model.transcribe(wav_io, beam_size=5)
        
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