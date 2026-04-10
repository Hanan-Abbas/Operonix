"""
voice/audio_manager.py — Operonix AI OS Agent
═══════════════════════════════════════════════
Centralised microphone controller.  One instance is created by the
Orchestrator and shared across WakeWordDetector, VoicePipeline, and
VoiceListener — the single source of truth for all audio input.

Robustness improvements vs original:
  • Retries open with exponential back-off on device error
  • Auto-reconnect if stream silently dies
  • Volume boost applied in read_chunk (controlled by MIC_VOLUME_BOOST)
  • Overflow counter exposed for diagnostics
  • Thread-safe start / stop with a lock
"""
from __future__ import annotations

import logging
import threading
import time
from typing import Optional

import numpy as np
import sounddevice as sd

from core.config import settings

logger = logging.getLogger("AudioManager")

_MAX_OPEN_RETRIES = 3
_RETRY_DELAY_S = 1.0


class AudioManager:
    """🎤 Centralised microphone controller (single source of truth)."""

    def __init__(
        self,
        rate: Optional[int] = None,
        chunk: Optional[int] = None,
        auto_start: bool = True,
    ) -> None:
        self._lock = threading.Lock()

        self.device: Optional[int] = settings.AUDIO_INPUT_INDEX  # None = OS default
        native_rate = self._query_native_rate()

        self.rate: int = rate or getattr(settings, "AUDIO_RATE", None) or native_rate
        self.chunk: int = chunk or getattr(settings, "AUDIO_CHUNK", 1280)

        self.stream: Optional[sd.InputStream] = None
        self.is_running: bool = False
        self.overflow_count: int = 0

        if auto_start:
            self.start()

    # ── Stream lifecycle ──────────────────────────────────────────────────────

    def start(self) -> bool:
        """Open the mic stream.  Returns True on success."""
        with self._lock:
            if self.is_running:
                return True

            device_label = self.device if self.device is not None else "default"
            logger.info("🎤 AudioManager: Opening device %s @ %d Hz …", device_label, self.rate)

            for attempt in range(1, _MAX_OPEN_RETRIES + 1):
                try:
                    self.stream = sd.InputStream(
                        samplerate=self.rate,
                        channels=1,
                        dtype="int16",
                        device=self.device,
                        blocksize=self.chunk,
                        latency="low",
                    )
                    self.stream.start()
                    self.is_running = True
                    logger.info("✅ AudioManager: Mic is LIVE (device=%s, rate=%d, chunk=%d).",
                                device_label, self.rate, self.chunk)
                    return True
                except sd.PortAudioError as exc:
                    logger.warning(
                        "AudioManager: open attempt %d/%d failed — %s",
                        attempt, _MAX_OPEN_RETRIES, exc,
                    )
                    if attempt < _MAX_OPEN_RETRIES:
                        time.sleep(_RETRY_DELAY_S * attempt)
                except Exception as exc:
                    logger.error("AudioManager: unexpected error opening stream — %s", exc)
                    break

            logger.error("❌ AudioManager: Could not open mic after %d attempts.", _MAX_OPEN_RETRIES)
            self.is_running = False
            return False

    def stop(self) -> None:
        """Stop and close the stream cleanly."""
        with self._lock:
            if self.stream is not None:
                try:
                    self.stream.stop()
                    self.stream.close()
                    logger.info("🛑 AudioManager: Stream stopped.")
                except Exception as exc:
                    logger.warning("AudioManager: error while closing stream — %s", exc)
                finally:
                    self.stream = None
            self.is_running = False

    def restart(self) -> bool:
        """Stop then start — useful after a device error."""
        self.stop()
        time.sleep(0.25)
        return self.start()

    # ── Audio reading ─────────────────────────────────────────────────────────

    def read_chunk(self) -> Optional[np.ndarray]:
        """
        Read one chunk of int16 audio (shape: [chunk, 1]).

        Returns None on failure (caller should check and continue).
        Auto-restarts the stream once if it detects a dead stream.
        """
        if not self.is_running:
            return None

        try:
            data, overflowed = self.stream.read(self.chunk)
            if overflowed:
                self.overflow_count += 1

            audio = data.copy()  # shape (chunk, 1), dtype int16

            # Apply software gain (boost weak laptop mics)
            boost = float(getattr(settings, "MIC_VOLUME_BOOST", 1.0))
            if boost != 1.0:
                boosted = audio.astype(np.float32) * boost
                audio = np.clip(boosted, -32768, 32767).astype(np.int16)

            return audio

        except sd.PortAudioError as exc:
            logger.warning("AudioManager: stream read error (%s) — attempting restart.", exc)
            self.is_running = False
            if self.restart():
                logger.info("AudioManager: stream recovered.")
            return None

        except Exception as exc:
            logger.error("AudioManager: unexpected read error — %s", exc)
            return None

    def clear_buffer(self, num_chunks: int = 5) -> None:
        """Discard stale audio accumulated while the agent was not listening."""
        for _ in range(max(1, num_chunks)):
            self.read_chunk()

    # ── Diagnostics ───────────────────────────────────────────────────────────

    def _query_native_rate(self) -> int:
        try:
            info = sd.query_devices(self.device, "input")
            return int(info["default_samplerate"])
        except Exception:
            return 16000

    def device_info(self) -> dict:
        """Return a summary of the active device for logging/debug."""
        try:
            info = sd.query_devices(self.device, "input")
            return {
                "name": info.get("name"),
                "index": self.device,
                "rate": self.rate,
                "chunk": self.chunk,
                "running": self.is_running,
                "overflows": self.overflow_count,
            }
        except Exception:
            return {"index": self.device, "running": self.is_running}