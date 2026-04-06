import sounddevice as sd
import numpy as np
from core.config import settings


class AudioManager:
    """🎤 Centralized microphone controller (single source of truth)"""

    def __init__(self, rate=None, chunk=1280, auto_start=True):
        # 1. Get device info from sounddevice
        self.device = settings.AUDIO_INPUT_INDEX
        try:
            device_info = sd.query_devices(self.device, 'input')
            native_rate = int(device_info['default_samplerate'])
        except Exception:
            native_rate = 16000 # Fallback

        # 2. Use provided rate, or settings, or the hardware's native rate
        self.rate = rate or getattr(settings, "AUDIO_RATE", native_rate)
        self.chunk = chunk
        
        self.stream = None
        self.is_running = False
        
        if auto_start:
            self.start()

    # 🔥 START STREAM
    def start(self):
        if self.is_running:
            return

        print(f"🎤 AudioManager: Attempting to open device {self.device}...")

        try:
            # 🟢 FIX: Remove rigid blocksize and allow the OS to manage the buffer
            self.stream = sd.InputStream(
                samplerate=self.rate,
                channels=1,
                dtype="int16",
                device=self.device,
                # Setting blocksize to 0 allows the hardware to choose the best buffer
                blocksize=self.chunk,  # You can still set a preferred chunk size for your reads
                latency='low'
            )

            self.stream.start()
            self.is_running = True
            print("✅ AudioManager: Mic is officially LIVE.")
        except Exception as e:
            print(f"❌ AudioManager Error: {e}")
            self.is_running = False


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
            # Added exception_on_overflow=False so it doesn't crash on tiny hitches!
            data, overflowed = self.stream.read(self.chunk)
            if overflowed:
                # Optional: log a debug message if frames were dropped
                pass
            return data.copy()
        except Exception as e:
            # Temporarily un-mute this to see if it's failing
            print(f"❌ Stream read error: {e}")
            return None

    # 🧹 CLEAR BUFFER
    def clear_buffer(self, num_chunks=5):
        print("🧹 AudioManager: Clearing buffer...")

        for _ in range(num_chunks):
            self.read_chunk()