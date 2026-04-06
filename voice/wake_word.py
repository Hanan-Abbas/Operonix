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
        
        # 🟢 FIX: Initialize loop variable to prevent AttributeError
        self.loop = None 

    def set_trigger_callback(self, callback):
        self.on_wake = callback

    def detect(self):
        if not self.audio_manager.is_running:
            return 0.0

        chunk = self.audio_manager.read_chunk()
        if chunk is None:
            return 0.0

        try:
            # 1. Get audio data
            audio_int16 = chunk.flatten().astype(np.int16)

            # 2. Predict
            prediction = self.model.predict(audio_int16)
            score = prediction.get(self.wake_word, 0)

            # 🟢 DEBUG: Print score on the same line so it doesn't flood the terminal
            if score > 0.01:
                print(f"🔍 Alexa Score: {score:.4f}", end="\r", flush=True)

            now = time.time()
            if score > self.trigger_threshold and (now - self.last_trigger_time > self.cooldown):
                self.last_trigger_time = now
                self.logger.info(f"\n🔔 DETECTED: {self.wake_word}")

                # Emit event safely
                if self.loop and self.loop.is_running():
                    self.loop.call_soon_threadsafe(
                        lambda: asyncio.create_task(bus.emit("wake_word_detected", {"trigger": self.wake_word, "score": score}))
                    )
                return score
                
            return 0.0
            
        except Exception as e:
            # Silent fail for audio glitches to prevent crash loops
            return 0.0