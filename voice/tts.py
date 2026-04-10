"""
voice/tts.py — Operonix AI OS Agent
═════════════════════════════════════
Text-to-speech via pyttsx3.  All properties (rate, volume, voice index)
are read from settings / environment variables so they can be tuned per
device without touching source code.
"""
from __future__ import annotations

import logging
from typing import Optional

from core.config import settings
from core.event_bus import bus

logger = logging.getLogger("TTS")


class TextToSpeech:
    def __init__(self) -> None:
        logger.info("🔊 TTS: Initialising voice engine …")
        import pyttsx3
        self.engine = pyttsx3.init()

        rate: int = int(getattr(settings, "TTS_RATE", 175))
        volume: float = float(getattr(settings, "TTS_VOLUME", 1.0))
        voice_index: int = int(getattr(settings, "TTS_VOICE_INDEX", 0))

        self.engine.setProperty("rate", rate)
        self.engine.setProperty("volume", volume)

        voices = self.engine.getProperty("voices")
        if voices:
            idx = min(voice_index, len(voices) - 1)
            self.engine.setProperty("voice", voices[idx].id)
            logger.info("TTS: voice[%d] = %s", idx, voices[idx].name)
        else:
            logger.warning("TTS: no voices found — using engine default.")

        logger.info("TTS: ready (rate=%d, volume=%.1f).", rate, volume)

    def speak(self, text: str) -> None:
        if not text:
            return
        logger.info("🤖 Agent says: %s", text)
        bus.publish("ai_speaking_started", {"text": text})
        self.engine.say(text)
        self.engine.runAndWait()
        bus.publish("ai_speaking_finished", {"status": "idle"})


# ── Smoke test ────────────────────────────────────────────────────────────────
if __name__ == "__main__":
    tts = TextToSpeech()
    tts.speak("Hello! Voice system is online and ready.")