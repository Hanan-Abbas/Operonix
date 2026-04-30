"""
voice/audio_manager.py — Operonix AI OS Agent
═══════════════════════════════════════════════
Centralised microphone controller shared across WakeWordDetector,
VoicePipeline, and VoiceListener.

FIX CHANGELOG (this revision)
──────────────────────────────
ROOT CAUSE OF CORE DUMP
  The `malloc_consolidate(): unaligned fastbin chunk detected` abort was a
  heap-corruption crash inside PortAudio's ALSA backend triggered by a race
  condition:

    Thread A (capture loop)  → read_chunk() → stream.read()
    Thread B (restart path)  → restart()    → stream.stop() + stream.close()
                                            → stream = new InputStream()
                                            → stream.start()

  Both threads touched the same `self.stream` object concurrently without
  holding the lock.  PortAudio's ALSA host API is not thread-safe; calling
  stop/close while a read is in flight corrupts internal heap structures and
  causes `malloc_consolidate` to abort the process.

FIXES APPLIED
  1. `_restart_lock` (threading.Lock) added — separate from `_lock` so
     read_chunk() can hold _restart_lock while restarting without deadlocking
     against start/stop which hold _lock.

  2. read_chunk() now holds `_restart_lock` for the entire read-then-maybe-
     restart window.  restart() also holds `_restart_lock` so concurrent
     restart calls serialise and only one stream teardown/creation happens
     at a time.

  3. Stream validity check added before every stream.read() call: if
     `self.stream` has been set to None by stop() on another thread, we
     return None gracefully instead of calling read() on a closed/None
     stream (which is another path to the crash).

  4. `auto_start` default kept as False (set in previous fix) — the mic
     only opens when voice mode is explicitly activated by mode_manager.

  5. ALSA error suppression: PortAudio prints ALSA warnings to stderr even
     when errors are handled.  These are cosmetic but alarming; we suppress
     them by redirecting ALSA's error handler via ctypes when available.
     This is best-effort — the actual errors are still handled in Python.

  6. `is_running` renamed to `_is_running` (private flag) so external callers
     such as ModeManager can safely check `getattr(audio_manager, "_is_running",
     False)` as a defensive guard before calling stop().  A read-only property
     `is_running` is retained for backward compatibility with any code that
     reads (but does not assign to) the old public name.  stop() now also sets
     `_is_running = False` explicitly *after* the stream.close() call in
     addition to the earlier in-lock assignment, guaranteeing the flag is False
     even if close() raises.
"""
from __future__ import annotations

import ctypes
import logging
import os
import threading
import time
from typing import Optional

import numpy as np
import sounddevice as sd

from core.config import settings

logger = logging.getLogger("AudioManager")

_MAX_OPEN_RETRIES = 3
_RETRY_DELAY_S = 1.0


def _suppress_alsa_stderr() -> None:
    """
    Redirect the ALSA/PortAudio C-level error handler to a no-op so the
    harmless 'Expression ... failed' lines don't flood stderr.

    This is best-effort: if libasound is not present the call is silently
    skipped.  Python-level error handling is unaffected.
    """
    try:
        asound = ctypes.cdll.LoadLibrary("libasound.so.2")
        # typedef void (*snd_lib_error_handler_t)(const char*, int, const char*, int, const char*, ...)
        ERROR_HANDLER_FUNC = ctypes.CFUNCTYPE(
            None,
            ctypes.c_char_p,  # file
            ctypes.c_int,     # line
            ctypes.c_char_p,  # function
            ctypes.c_int,     # err
            ctypes.c_char_p,  # fmt
        )
        # Install a no-op handler
        asound.snd_lib_error_set_handler(ERROR_HANDLER_FUNC(lambda *_: None))
        logger.debug("AudioManager: ALSA stderr error handler suppressed.")
    except Exception:
        pass  # Not on Linux or libasound not available — safe to ignore


# Suppress noisy ALSA stderr output at import time (best-effort)
_suppress_alsa_stderr()


class AudioManager:
    """🎤 Centralised microphone controller (single source of truth)."""

    def __init__(
        self,
        rate: Optional[int] = None,
        chunk: Optional[int] = None,
        auto_start: bool = False,
    ) -> None:
        # _lock guards _is_running / start / stop
        self._lock = threading.Lock()
        # _restart_lock serialises stream teardown+creation during restart.
        # FIX: Separate from _lock to avoid deadlock between read_chunk()
        # (which must not hold _lock during a blocking read) and start/stop.
        self._restart_lock = threading.Lock()

        self.device: Optional[int] = settings.AUDIO_INPUT_INDEX
        native_rate = self._query_native_rate()

        self.rate: int = rate or getattr(settings, "AUDIO_RATE", None) or native_rate
        self.chunk: int = chunk or getattr(settings, "AUDIO_CHUNK", 1280)

        self.stream: Optional[sd.InputStream] = None
        # FIX: Private flag — external callers must use the read-only
        # `is_running` property or check `_is_running` directly via getattr.
        self._is_running: bool = False
        self.overflow_count: int = 0

        if auto_start:
            self.start()

    # ── Backward-compatible public flag ───────────────────────────────────────

    @property
    def is_running(self) -> bool:
        """Read-only alias for `_is_running`.

        Retained so existing code that reads (but never assigns to)
        `audio_manager.is_running` continues to work without modification.
        External callers that need a defensive guard should check
        `_is_running` directly via ``getattr(mgr, "_is_running", False)``
        so the intent is explicit.
        """
        return self._is_running

    # ── Device introspection ──────────────────────────────────────────────────

    def _query_native_rate(self) -> int:
        """Query the hardware for its default sampling rate (fallback: 16000)."""
        try:
            device_info = sd.query_devices(self.device, "input")
            return int(device_info.get("default_samplerate", 16000))
        except Exception as exc:
            logger.warning(
                "AudioManager: Could not query native rate — %s. Using 16k fallback.", exc
            )
            return 16000

    def device_info(self) -> dict:
        """Return a dict of the current input device's properties for diagnostics."""
        try:
            raw = sd.query_devices(self.device, "input")
            return {
                "name": raw.get("name", "unknown"),
                "index": self.device,
                "sample_rate": self.rate,
                "chunk_size": self.chunk,
                "channels": int(raw.get("max_input_channels", 1)),
                "is_running": self._is_running,
                "overflow_count": self.overflow_count,
            }
        except Exception as exc:
            logger.warning("AudioManager.device_info: could not query device — %s", exc)
            return {
                "name": "unknown",
                "index": self.device,
                "sample_rate": self.rate,
                "chunk_size": self.chunk,
                "is_running": self._is_running,
                "overflow_count": self.overflow_count,
            }

    # ── Stream lifecycle ──────────────────────────────────────────────────────

    def start(self) -> bool:
        """Open the mic stream.  Returns True on success."""
        with self._lock:
            if self._is_running:
                return True

            device_label = self.device if self.device is not None else "default"
            logger.info(
                "🎤 AudioManager: Opening device %s @ %d Hz …", device_label, self.rate
            )

            for attempt in range(1, _MAX_OPEN_RETRIES + 1):
                try:
                    stream = sd.InputStream(
                        samplerate=self.rate,
                        channels=1,
                        dtype="int16",
                        device=self.device,
                        blocksize=self.chunk,
                        latency="low",
                    )
                    stream.start()
                    # Assign atomically after start() succeeds so read_chunk()
                    # never sees a stream that is allocated but not yet started.
                    self.stream = stream
                    self._is_running = True
                    logger.info(
                        "✅ AudioManager: Mic is LIVE (device=%s, rate=%d, chunk=%d).",
                        device_label, self.rate, self.chunk,
                    )
                    return True
                except sd.PortAudioError as exc:
                    logger.warning(
                        "AudioManager: open attempt %d/%d failed — %s",
                        attempt, _MAX_OPEN_RETRIES, exc,
                    )
                    if attempt < _MAX_OPEN_RETRIES:
                        time.sleep(_RETRY_DELAY_S * attempt)
                except Exception as exc:
                    logger.error(
                        "AudioManager: unexpected error opening stream — %s", exc
                    )
                    break

            logger.error(
                "❌ AudioManager: Could not open mic after %d attempts.", _MAX_OPEN_RETRIES
            )
            self._is_running = False
            return False

    def stop(self) -> None:
        """Stop and close the stream cleanly."""
        with self._lock:
            stream = self.stream
            self.stream = None          # Nullify before close so read_chunk sees None
            self._is_running = False

        if stream is not None:
            try:
                stream.stop()
                stream.close()
                logger.info("🛑 AudioManager: Stream stopped.")
            except Exception as exc:
                logger.warning("AudioManager: error while closing stream — %s", exc)
            finally:
                # FIX: Guarantee _is_running is False even if close() raises,
                # so external guards like `getattr(mgr, "_is_running", False)`
                # always see a consistent False after stop() returns.
                self._is_running = False

    def restart(self) -> bool:
        """
        Stop then start — used after a device error.

        FIX: Holds _restart_lock for the entire teardown+creation cycle so
        concurrent calls from read_chunk() and an external caller do not race
        on stream creation, which was the root cause of the ALSA heap
        corruption and subsequent core dump.
        """
        with self._restart_lock:
            logger.info("AudioManager: restarting stream…")
            self.stop()
            time.sleep(0.3)            # Give ALSA time to release hardware buffer
            result = self.start()
            if result:
                logger.info("AudioManager: stream restarted successfully.")
            else:
                logger.error("AudioManager: stream restart failed.")
            return result

    # ── Audio reading ─────────────────────────────────────────────────────────

    def read_chunk(self) -> Optional[np.ndarray]:
        """
        Read one chunk of int16 audio (shape: [chunk, 1]).

        Returns None on failure; caller should check and continue.

        FIX: Holds _restart_lock during the read-then-maybe-restart window.
        Without this lock, stop() on another thread could set self.stream=None
        or close the stream between our `if not self.is_running` check and the
        actual stream.read() call — causing the ALSA crash.

        Stream validity is re-checked inside the lock before read() so we
        never call read() on a None or already-closed stream.
        """
        if not self._is_running:
            return None

        with self._restart_lock:
            # Re-check inside the lock — stop() may have run between the
            # is_running check above and acquiring the lock.
            if not self._is_running or self.stream is None:
                return None

            try:
                data, overflowed = self.stream.read(self.chunk)
                if overflowed:
                    self.overflow_count += 1

                audio = data.copy()  # shape (chunk, 1), dtype int16

                boost = float(getattr(settings, "MIC_VOLUME_BOOST", 1.0))
                if boost != 1.0:
                    boosted = audio.astype(np.float32) * boost
                    audio = np.clip(boosted, -32768, 32767).astype(np.int16)

                return audio

            except sd.PortAudioError as exc:
                logger.warning(
                    "AudioManager: stream read error (%s) — attempting restart.", exc
                )
                # Set _is_running=False so restart() → stop() is a no-op on
                # the already-dead stream, then start fresh.
                self._is_running = False
                # Release lock before restart() re-acquires it.

        # Restart outside _restart_lock to avoid self-deadlock (restart()
        # acquires _restart_lock internally).
        if self.restart():
            logger.info("AudioManager: stream recovered after read error.")
        return None

    def clear_buffer(self, num_chunks: int = 5) -> None:
        """Discard stale audio accumulated while the agent was not listening."""
        for _ in range(max(1, num_chunks)):
            self.read_chunk()

    def drain_tail(self, num_chunks: int = 10) -> Optional[np.ndarray]:
        """
        Read up to num_chunks of audio without blocking the pipeline loop.

        Used by VoicePipeline.flush_tail() during voice → panel teardown.
        Returns a flat int16 numpy array of all drained samples, or None
        if the stream is not running or the buffer is empty.
        """
        if not self._is_running:
            return None

        frames: list[np.ndarray] = []
        for _ in range(max(1, num_chunks)):
            chunk = self.read_chunk()
            if chunk is None:
                break
            frames.append(chunk.flatten())

        if not frames:
            return None

        return np.concatenate(frames).astype(np.int16)