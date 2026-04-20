"""
voice/noise_filter.py — Operonix AI OS Agent
═════════════════════════════════════════════
Lightweight spectral-gating noise filter using noisereduce.

This is the *fallback* filter used when DeepFilterNet is unavailable.
For the full neural-backed filter (CPU DeepFilterNet), see
voice/deep_noise_filter.py.

Self-contained — no imports from core/ or voice/ to avoid circular deps.
"""
from __future__ import annotations

import logging
from typing import Optional

import numpy as np
import noisereduce as nr

logger = logging.getLogger("NoiseFilter")


class NoiseFilter:
    """
    Spectral-gating noise reduction via noisereduce.

    Maintains a static ambient noise profile generated at construction
    time.  Callers may optionally supply a live noise profile (e.g. a
    pre-roll capture before the user speaks) for better accuracy.
    """

    def __init__(self, rate: int = 16000) -> None:
        self.rate = rate if rate is not None else 16000
        self._static_noise_profile: np.ndarray = self._generate_static_profile()
        logger.info("🎙️ NoiseFilter (spectral gating): initialised at %d Hz", self.rate)

    # ── Public API ────────────────────────────────────────────────────────────

    def reduce_noise(
        self,
        audio_float32: np.ndarray,
        noise_profile: Optional[np.ndarray] = None,
        prop_decrease: float = 0.85,
        n_std: float = 1.5,
    ) -> np.ndarray:
        """
        Apply spectral-gating noise reduction and return cleaned float32 audio.

        Args:
            audio_float32:  1-D float32 array normalised to [-1, 1].
            noise_profile:  Optional live noise reference (pre-roll capture).
                            Falls back to the static profile when not provided.
            prop_decrease:  Proportion of noise to reduce (0–1).
            n_std:          Noise gate threshold in standard deviations.
        """
        if audio_float32 is None or len(audio_float32) < 512:
            return audio_float32

        audio_float32 = np.clip(audio_float32.astype(np.float32), -1.0, 1.0)

        # Prefer a live noise profile when one is supplied and long enough.
        y_noise = (
            noise_profile
            if (noise_profile is not None and noise_profile.size >= 512)
            else self._static_noise_profile
        )

        try:
            if y_noise is not None and y_noise.size >= 512:
                cleaned = nr.reduce_noise(
                    y=audio_float32,
                    sr=self.rate,
                    y_noise=y_noise,
                    stationary=True,
                    n_std_thresh_stationary=n_std,
                    prop_decrease=prop_decrease,
                )
            else:
                # No noise profile — use non-stationary mode as last resort.
                cleaned = nr.reduce_noise(
                    y=audio_float32,
                    sr=self.rate,
                    stationary=False,
                    prop_decrease=min(0.55, prop_decrease),
                )
            return np.clip(cleaned, -1.0, 1.0).astype(np.float32)
        except Exception as exc:
            logger.warning("NoiseFilter: noisereduce failed (%s) — returning raw audio.", exc)
            return audio_float32

    # ── Internal helpers ──────────────────────────────────────────────────────

    def _generate_static_profile(self) -> np.ndarray:
        """White-noise stand-in for ambient background (used as last resort)."""
        noise = np.random.normal(0, 0.05, int(self.rate * 0.5))
        return noise.astype(np.float32)