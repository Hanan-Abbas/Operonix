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
        self.audio_queue = queue.Queue(maxsize=10)

        # Cooldown to prevent immediate re-trigger
        self.last_trigger_time = 0
        self.cooldown = 3

        # Callback when wake word is detected
        self.on_wake = None

    def pause(self):
        """Pauses queue filling so command listener can use the stream."""
        print("⏸️ WakeWordDetector: Pausing queue...")
        while not self.audio_queue.empty():
            try:
                self.audio_queue.get_nowait()
            except queue.Empty:
                break

    def resume(self):
        """Resumes wake word detection and clears openWakeWord's memory."""
        print("▶️ WakeWordDetector: Resuming and wiping model memory...")

        # Reset the internal states of openWakeWord
        self.model.reset()

        # Hard 2-second ignore window from THIS exact moment to prevent ghost loops
        self.last_trigger_time = time.time() + 2.0

        # Clear the local queue
        while not self.audio_queue.empty():
            try:
                self.audio_queue.get_nowait()
            except queue.Empty:
                break

    def set_trigger_callback(self, callback):
        self.on_wake = callback

    def detect(self):
        """Check audio from AudioManager and detect wake word."""
        if not self.audio_manager.is_running:
            return 0.0

        # 1. Grab a chunk from AudioManager (exactly 512 samples)
        chunk = self.audio_manager.read_chunk()
        if chunk is None:
            return 0.0

        # 2. Get the raw 16-bit integers
        audio_int16 = chunk.astype(np.int16).flatten()

        # 🟢 FIX: Boost the volume by 1.5x for the wake word engine too!
        audio_int16 = np.clip(audio_int16 * 1.5, -32768, 32767).astype(np.int16)

        # 3. Predict using openWakeWord
        prediction = self.model.predict(audio_int16)
        score = prediction.get(self.wake_word, 0)

        # Print debug score
        print(f"Debug Score: {score:.4f}")

        # 4. Cooldown check
        now = time.time()
        if now - self.last_trigger_time < self.cooldown:
            return 0.0

        # 🟢 FIX: Lowered threshold to 0.25 because of low mic input
        if score > 0.25:
            self.last_trigger_time = now
            print(f"\n🔔 Wake Word Detected: {self.wake_word} ({score:.2f})")

            # Fire callback if set
            if self.on_wake:
                self.on_wake()

            return score

        return 0.0