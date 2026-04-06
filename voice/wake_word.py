import queue
import time
import logging
import asyncio
import numpy as np
from openwakeword.model import Model
from core.config import settings
from core.event_bus import bus

class WakeWordDetector:
    """👂 Wake Word detector using shared AudioManager stream."""

    def __init__(self, wake_word=None, audio_manager=None):
        if audio_manager is None:
            raise ValueError("WakeWordDetector requires an AudioManager instance")

        self.logger = logging.getLogger("WakeWordDetector")
        self.wake_word = wake_word or getattr(settings, "WAKE_WORD", "alexa")
        
        self.model = Model()
        self.audio_manager = audio_manager
        
        # Pull configuration from settings
        self.last_trigger_time = 0
        self.cooldown = getattr(settings, "WAKE_COOLDOWN", 3)
        self.trigger_threshold = getattr(settings, "WAKE_THRESHOLD", 0.05)
        self.on_wake = None

    def set_trigger_callback(self, callback):
        self.on_wake = callback

    def detect(self):
        if not self.audio_manager.is_running:
            return 0.0

        chunk = self.audio_manager.read_chunk()
        if chunk is None:
            return 0.0

        try:
            # 1. Flatten the raw hardware audio
            audio_data = chunk.flatten()

            # 2. DYNAMIC RESAMPLING
            # Calculate the ratio between hardware and model (16000)
            native_rate = self.audio_manager.rate
            target_rate = 16000
            
            if native_rate != target_rate:
                # Calculate how many samples we need to keep
                # e.g., if native is 48000, we keep 1 out of every 3 samples
                step = native_rate // target_rate
                audio_int16 = audio_data[::step].astype(np.int16)
            else:
                audio_int16 = audio_data.astype(np.int16)
            prediction = self.model.predict(audio_int16)
            score = prediction.get(self.wake_word, 0)

            # 🟢 DEBUG: Add a simple print to see if it's alive
            if score > 0.01:
                print(f"🔍 Score: {score:.4f}", end="\r")

            now = time.time()
            if score > self.trigger_threshold and (now - self.last_trigger_time > self.cooldown):
                self.last_trigger_time = now
                self.logger.info(f"🔔 DETECTED: {self.wake_word}")

                # 🟢 FIX: Reliable event emission
                if self.loop and self.loop.is_running():
                    self.loop.call_soon_threadsafe(
                        lambda: asyncio.create_task(bus.emit("wake_word_detected", {"trigger": self.wake_word, "score": score}))
                    )
                return score
            return 0.0
        except Exception as e:
            return 0.0