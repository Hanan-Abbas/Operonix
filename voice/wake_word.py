import queue
import sys
import time
import numpy as np
import sounddevice as sd
from openwakeword.model import Model
from core.config import settings


class WakeWordDetector:

    def __init__(self, wake_word="alexa"):
        print(f"👂 Wake Word: Initializing detector for '{wake_word}'...")

        self.wake_word = wake_word
        self.model = Model()
        self.rate = 16000

        self.audio_queue = queue.Queue(maxsize=10)  # 🔥 prevent overflow

        self.stream = None
        self.is_running = False

        # 🔥 Cooldown
        self.last_trigger_time = 0
        self.cooldown = 3  # seconds

    def set_trigger_callback(self, callback):
        self.on_wake = callback

    def _audio_callback(self, indata, frames, time_info, status):
        if status:
            print(f"⚠️ Audio status: {status}", file=sys.stderr)

        try:
            self.audio_queue.put_nowait(indata.copy().flatten())
        except queue.Full:
            pass  # 🔥 drop old data instead of blocking

    # ✅ START STREAM (non-blocking)
    def start(self):
        if self.is_running:
            return

        print("👂 Wake Word: Started")
        self.stream = sd.InputStream(
            samplerate=self.rate,
            channels=1,
            dtype="int16",
            device=settings.AUDIO_INPUT_INDEX,
            callback=self._audio_callback,
            blocksize=1280,
        )
        self.stream.start()
        self.is_running = True

    # ✅ STOP STREAM
    def stop(self):
        if self.stream:
            print("🛑 Wake Word: Stopped")
            self.stream.stop()
            self.stream.close()
        self.is_running = False

    # ✅ ALIAS (clean naming)
    def pause(self):
        self.stop()

    def resume(self):
        self.start()

    # ✅ SINGLE STEP DETECTION (NO LOOP!)
    def detect(self):
        """Process one audio chunk and return confidence"""

        if not self.is_running:
            return 0.0

        if self.audio_queue.empty():
            return 0.0

        audio_chunk = self.audio_queue.get()
        audio_list = audio_chunk.tolist()

        prediction = self.model.predict(audio_list)
        score = prediction.get(self.wake_word, 0)

        print(f"Debug Score: {score:.4f}", end="\r")

        # 🔥 Cooldown protection
        now = time.time()
        if now - self.last_trigger_time < self.cooldown:
            return 0.0

        if score > 0.85:
            self.last_trigger_time = now
            print(f"\n🔔 Wake Word Detected: {self.wake_word} ({score:.2f})")

            if hasattr(self, "on_wake") and self.on_wake:
                self.on_wake()

            return score

        return 0.0