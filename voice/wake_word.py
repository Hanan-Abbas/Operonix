import queue
import time
import numpy as np
from openwakeword.model import Model


class WakeWordDetector:
    """👂 Wake Word detector using shared AudioManager stream"""

    def __init__(self, wake_word="alexa", audio_manager=None):
        if audio_manager is None:
            raise ValueError("WakeWordDetector requires an AudioManager instance")

        print(f"👂 Wake Word: Initializing detector for '{wake_word}'...")

        self.wake_word = wake_word
        self.model = Model()
        self.rate = 16000

        self.audio_manager = audio_manager
        self.audio_queue = queue.Queue(maxsize=10)  # buffer for detection

        # Cooldown to prevent retrigger
        self.last_trigger_time = 0
        self.cooldown = 3  # seconds

        # Optional callback when wake word is detected
        self.on_wake = None

    def pause(self):
        """Pauses queue filling so command listener can use the stream."""
        print("⏸️ WakeWordDetector: Pausing queue...")
        # Clear the queue so old audio doesn't cause fake triggers later
        while not self.audio_queue.empty():
            try:
                self.audio_queue.get_nowait()
            except queue.Empty:
                break

    def resume(self):
        """Resumes wake word detection and clears openWakeWord's memory."""
        print("▶️ WakeWordDetector: Resuming and wiping model memory...")

        # 1. Reset the internal states of openWakeWord
        self.model.reset()

        # 🟢 FIX: Set a timestamp to ignore detection for the next 1.5 seconds
        self.last_trigger_time = time.time() + 1.5

        # Clear the local queue
        while not self.audio_queue.empty():
            try:
                self.audio_queue.get_nowait()
            except queue.Empty:
                break

    def set_trigger_callback(self, callback):
        self.on_wake = callback

    # ✅ SINGLE STEP DETECTION
    def detect(self):
        """Check one chunk from AudioManager and detect wake word."""
        if not self.audio_manager.is_running:
            return 0.0

        # Grab a chunk from AudioManager
        chunk = self.audio_manager.read_chunk()
        if chunk is None:
            return 0.0

        # Feed to local queue
        try:
            self.audio_queue.put_nowait(chunk.flatten())
        except queue.Full:
            pass  # drop old data

        if self.audio_queue.empty():
            return 0.0

        audio_chunk = self.audio_queue.get()
        audio_list = audio_chunk.tolist()

        prediction = self.model.predict(audio_list)
        score = prediction.get(self.wake_word, 0)

        print(f"Debug Score: {score:.4f}", end="\r")

        # Cooldown check
        now = time.time()
        if now - self.last_trigger_time < self.cooldown:
            return 0.0

        if score > 0.75:
            self.last_trigger_time = now
            print(f"\n🔔 Wake Word Detected: {self.wake_word} ({score:.2f})")

            # Fire callback if set
            if self.on_wake:
                self.on_wake()

            return score

        return 0.0