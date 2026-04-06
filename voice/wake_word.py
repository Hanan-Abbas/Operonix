import queue
import time
import logging
import numpy as np
from openwakeword.model import Model
from core.config import settings


class WakeWordDetector:
    """👂 Wake Word detector using shared AudioManager stream.
    Zero hardcoded strings or parameters.
    """

    def __init__(self, wake_word=None, audio_manager=None):
        if audio_manager is None:
            raise ValueError("WakeWordDetector requires an AudioManager instance")

        self.logger = logging.getLogger("WakeWordDetector")
        
        # 🟢 DYNAMIC: Pull from settings if not passed manually
        self.wake_word = wake_word or getattr(settings, "WAKE_WORD", "alexa")
        self.logger.info(f"👂 Wake Word: Initializing detector for '{self.wake_word}'...")

        self.model = Model()
        
        # 🟢 DYNAMIC: Pull sample rate directly from the source of truth (AudioManager)
        self.rate = getattr(audio_manager, 'rate', 16000)

        self.audio_manager = audio_manager
        
        # Pull queue maxsize from settings if defined
        queue_size = getattr(settings, "AUDIO_QUEUE_MAX", 10)
        self.audio_queue = queue.Queue(maxsize=queue_size)

        # Cooldown to prevent immediate re-trigger
        self.last_trigger_time = 0
        self.cooldown = getattr(settings, "WAKE_COOLDOWN", 3)

        # Callback when wake word is detected
        self.on_wake = None

    def pause(self):
        """Pauses queue filling so command listener can use the stream."""
        self.logger.info("⏸️ WakeWordDetector: Pausing queue...")
        while not self.audio_queue.empty():
            try:
                self.audio_queue.get_nowait()
            except queue.Empty:
                break

    def resume(self):
        """Resumes wake word detection and clears openWakeWord's memory."""
        self.logger.info("▶️ WakeWordDetector: Resuming and wiping model memory...")

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

        # 1. Grab a chunk from AudioManager
        chunk = self.audio_manager.read_chunk()
        if chunk is None:
            return 0.0

        # 2. Get the raw 16-bit integers
        audio_int16 = chunk.astype(np.int16).flatten()

        # 🟢 DYNAMIC: Pull volume boost scale from settings (default to 1.5)
        volume_boost = getattr(settings, "MIC_VOLUME_BOOST", 1.5)
        audio_int16 = np.clip(audio_int16 * volume_boost, -32768, 32767).astype(np.int16)

        # 3. Predict using openWakeWord
        prediction = self.model.predict(audio_int16)
        score = prediction.get(self.wake_word, 0)

        # Print debug score (Turned to debug logging rather than plain prints)
        self.logger.debug(f"Debug Score: {score:.4f}")

        # 4. Cooldown check
        now = time.time()
        if now - self.last_trigger_time < self.cooldown:
            return 0.0

        # 🟢 DYNAMIC: Pull threshold limit from settings (default to 0.25)
        trigger_threshold = getattr(settings, "WAKE_THRESHOLD", 0.25)
        if score > trigger_threshold:
            self.last_trigger_time = now
            self.logger.info(f"\n🔔 Wake Word Detected: {self.wake_word} ({score:.2f})")

            # Fire callback if set
            if self.on_wake:
                self.on_wake()

            return score

        return 0.0