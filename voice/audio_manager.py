import sounddevice as sd
import numpy as np
from core.config import settings


class AudioManager:
    """🎤 Centralized microphone controller (single source of truth)"""

    def __init__(self, rate=16000, chunk=512):
        self.rate = rate
        self.chunk = chunk
        self.device = settings.AUDIO_INPUT_INDEX

        self.stream = None
        self.is_running = False

    # 🔥 START STREAM
    def start(self):
        if self.is_running:
            return

        print("🎤 AudioManager: Starting input stream...")

        self.stream = sd.InputStream(
            samplerate=self.rate,
            channels=1,
            dtype="int16",
            device=self.device,
            blocksize=self.chunk,
        )

        self.stream.start()
        self.is_running = True

    # 🛑 STOP STREAM
    def stop(self):
        if self.stream:
            print("🛑 AudioManager: Stopping stream...")
            self.stream.stop()
            self.stream.close()

        self.is_running = False

    # 📥 READ CHUNK
    def read_chunk(self):
        if not self.is_running:
            return None

        try:
            data, _ = self.stream.read(self.chunk)
            return data.copy()
        except Exception:
            return None

    # 🧹 CLEAR BUFFER
    def clear_buffer(self, num_chunks=5):
        print("🧹 AudioManager: Clearing buffer...")

        for _ in range(num_chunks):
            self.read_chunk()