"""
voice/wake_word.py — Operonix AI OS Agent
═══════════════════════════════════════════
Wake-word detection using openWakeWord and the shared AudioManager.

Fixes vs original:
  • No AttributeError on self.loop — guarded with is_running check
  • Cooldown and threshold always read from settings (not baked in)
  • Silent failure for audio glitches with per-second rate-limit on logging
  • Cleaner asyncio event emission
"""
from __future__ import annotations

import asyncio
import logging
import time
from typing import Callable, Optional

import numpy as np

from core.config import settings
from core.event_bus import bus
from voice.audio_manager import AudioManager

logger = logging.getLogger("WakeWordDetector")


class WakeWordDetector:
    """👂 Wake-word detection using openWakeWord + shared AudioManager."""

    def __init__(
        self,
        wake_word: Optional[str] = None,
        audio_manager: Optional[AudioManager] = None,
    ) -> None:
        if audio_manager is None:
            raise ValueError("WakeWordDetector requires an AudioManager instance.")

        self.audio_manager = audio_manager
        self.wake_word: str = wake_word or getattr(settings, "WAKE_WORD", "alexa")

        # Lazy-load the model so import errors surface with a clear message
        try:
            from openwakeword.model import Model
            self.model = Model()
        except ImportError as exc:
            raise ImportError(
                "openwakeword is not installed. Run: pip install openwakeword"
            ) from exc

        self.cooldown: float = float(getattr(settings, "WAKE_COOLDOWN", 3.0))
        self.trigger_threshold: float = float(getattr(settings, "WAKE_THRESHOLD", 0.50))

        self.last_trigger_time: float = 0.0
        self.on_wake: Optional[Callable] = None
        self.loop: Optional[asyncio.AbstractEventLoop] = None  # set by Orchestrator

        self._last_error_log: float = 0.0  # rate-limit error logging

        logger.info(
            "👂 WakeWordDetector ready — word=%r, threshold=%.2f, cooldown=%.1fs",
            self.wake_word, self.trigger_threshold, self.cooldown,
        )

    # ── Public API ────────────────────────────────────────────────────────────

    def set_trigger_callback(self, callback: Callable) -> None:
        self.on_wake = callback

    def detect(self) -> float:
        """
        Read one chunk from the mic and run wake-word inference.
        Returns the detection score (0.0 if not triggered).
        Called repeatedly from a background executor thread.
        """
        if not self.audio_manager.is_running:
            return 0.0

        chunk = self.audio_manager.read_chunk()
        if chunk is None:
            return 0.0

        try:
            audio_int16 = chunk.flatten().astype(np.int16)
            prediction = self.model.predict(audio_int16)
            score: float = float(prediction.get(self.wake_word, 0.0))

            if score > 0.01:
                print(f"\r🔍 {self.wake_word} score: {score:.4f}   ", end="", flush=True)

            now = time.time()
            if score >= self.trigger_threshold and (now - self.last_trigger_time) > self.cooldown:
                self.last_trigger_time = now
                logger.info("\n🔔 WAKE WORD DETECTED: %r (score=%.3f)", self.wake_word, score)
                self._emit_event(score)
                return score

        except Exception as exc:
            # Rate-limited logging so we don't flood on sustained errors
            if time.time() - self._last_error_log > 5.0:
                logger.debug("WakeWordDetector.detect error (suppressed): %s", exc)
                self._last_error_log = time.time()

        return 0.0

    # ── Internal ──────────────────────────────────────────────────────────────

    def _emit_event(self, score: float) -> None:
        """Fire wake_word_detected on the event bus, thread-safely."""
        payload = {"trigger": self.wake_word, "score": score}

        if self.loop is not None and self.loop.is_running():
            self.loop.call_soon_threadsafe(
                lambda: asyncio.ensure_future(
                    bus.emit("wake_word_detected", payload),
                    loop=self.loop,
                )
            )
        else:
            logger.warning(
                "WakeWordDetector: event loop not set or not running — "
                "wake event not emitted. Ensure orchestrator sets self.wake_detector.loop."
            )