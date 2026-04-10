"""
voice/deep_noise_filter.py — Operonix AI OS Agent
═══════════════════════════════════════════════════
Neural noise suppression using DeepFilterNet (DFN) with automatic
fallback to spectral-gating (noisereduce) when DFN is unavailable.

DeepFilterNet achieves near-studio cleanup of fan/HVAC/keyboard noise
without the voice-colouring artifacts that spectral gating introduces.

Install DFN once:
    pip install deepfilternet

If the package is absent the module silently falls back to noisereduce —
no code changes required anywhere else in the project.
"""
from __future__ import annotations

import logging
from typing import Optional

import numpy as np

from core.config import settings

logger = logging.getLogger("NoiseFilter")

# ── Attempt to load DeepFilterNet ─────────────────────────────────────────────
_DFN_AVAILABLE = False
_dfn_model = None
_dfn_state = None
_dfn_sr: int = 48000  # DFN operates at 48 kHz internally

try:
    from df.enhance import enhance, init_df, load_audio, save_audio
    from df.io import resample

    _dfn_model, _dfn_state, _ = init_df()
    _DFN_AVAILABLE = True
    logger.info("✅ DeepFilterNet loaded — neural noise suppression active.")
except Exception as _dfn_err:
    logger.warning(
        "⚠️  DeepFilterNet unavailable (%s). "
        "Install with: pip install deepfilternet  "
        "Falling back to spectral gating (noisereduce).",
        _dfn_err,
    )


# ── noisereduce fallback ───────────────────────────────────────────────────────
import noisereduce as _nr


def _resample_numpy(audio: np.ndarray, from_sr: int, to_sr: int) -> np.ndarray:
    """Simple linear-interpolation resampler (no extra deps)."""
    if from_sr == to_sr:
        return audio
    ratio = to_sr / from_sr
    out_len = max(1, int(len(audio) * ratio))
    return np.interp(
        np.linspace(0, len(audio) - 1, out_len),
        np.arange(len(audio)),
        audio,
    ).astype(np.float32)


class NoiseFilter:
    """
    Unified noise-reduction interface.

    Selects backend at construction time based on config:
        VOICE_DENOISE_BACKEND = "dfn"         → DeepFilterNet (best)
        VOICE_DENOISE_BACKEND = "noisereduce" → Spectral gating
        VOICE_DENOISE_BACKEND = "none"        → Pass-through

    If DFN is requested but unavailable, noisereduce is used automatically.
    """

    def __init__(self, rate: int = 16000) -> None:
        self.rate = rate if rate else 16000
        self._backend = self._resolve_backend()
        self._static_noise_profile: Optional[np.ndarray] = None

        if self._backend == "noisereduce":
            self._static_noise_profile = self._generate_static_profile()

        logger.info("🎙️ NoiseFilter ready — backend: %s", self._backend)

    # ── Public API ────────────────────────────────────────────────────────────

    def reduce_noise(
        self,
        audio_float32: np.ndarray,
        noise_profile: Optional[np.ndarray] = None,
    ) -> np.ndarray:
        """
        Apply noise reduction and return cleaned float32 audio.

        Args:
            audio_float32:  1-D float32 array normalised to [-1, 1], at self.rate.
            noise_profile:  Optional float32 array of background noise (pre-roll).
                            Used only by the noisereduce backend.
        """
        if audio_float32 is None or len(audio_float32) < 512:
            return audio_float32

        audio_float32 = np.clip(audio_float32.astype(np.float32), -1.0, 1.0)

        if self._backend == "dfn":
            return self._dfn_reduce(audio_float32)
        elif self._backend == "noisereduce":
            return self._nr_reduce(audio_float32, noise_profile)
        else:
            return audio_float32  # "none" — pass through

    # ── Backend resolution ────────────────────────────────────────────────────

    def _resolve_backend(self) -> str:
        requested = getattr(settings, "VOICE_DENOISE_BACKEND", "dfn").lower().strip()

        if requested == "none":
            return "none"

        if requested == "dfn":
            if _DFN_AVAILABLE:
                return "dfn"
            logger.warning(
                "DFN requested but not installed — using noisereduce. "
                "Run: pip install deepfilternet"
            )
            return "noisereduce"

        # "noisereduce" or any unrecognised value
        return "noisereduce"

    # ── DeepFilterNet path ────────────────────────────────────────────────────

    def _dfn_reduce(self, audio: np.ndarray) -> np.ndarray:
        """Run audio through the DeepFilterNet neural enhancer."""
        global _dfn_model, _dfn_state

        try:
            # DFN expects 48 kHz input
            audio_48k = _resample_numpy(audio, self.rate, _dfn_sr)

            # enhance() works on a (1, samples) tensor-like shape
            import torch
            tensor = torch.from_numpy(audio_48k).unsqueeze(0)

            atten_lim = float(getattr(settings, "DFN_ATTEN_LIM_DB", 40.0))
            post_filter = bool(getattr(settings, "DFN_POST_FILTER", True))

            enhanced_tensor = enhance(
                _dfn_model,
                _dfn_state,
                tensor,
                atten_lim_db=atten_lim,
                post_filter=post_filter,
            )
            enhanced_48k = enhanced_tensor.squeeze(0).numpy()

            # Resample back to pipeline rate
            enhanced = _resample_numpy(enhanced_48k, _dfn_sr, self.rate)
            return np.clip(enhanced, -1.0, 1.0).astype(np.float32)

        except Exception as exc:
            logger.warning("DFN enhance failed (%s) — skipping noise reduction.", exc)
            return audio

    # ── Spectral-gating path ──────────────────────────────────────────────────

    def _nr_reduce(
        self,
        audio: np.ndarray,
        noise_profile: Optional[np.ndarray] = None,
    ) -> np.ndarray:
        """Spectral-gating via noisereduce."""
        if not getattr(settings, "VOICE_DENOISE_ENABLED", True):
            return audio

        prop_decrease = float(getattr(settings, "VOICE_DENOISE_PROP_DECREASE", 0.65))
        n_std = float(getattr(settings, "VOICE_DENOISE_N_STD", 1.5))

        # Prefer a live noise profile over the static one (better accuracy)
        y_noise = noise_profile if (noise_profile is not None and noise_profile.size >= 512) \
                  else self._static_noise_profile

        try:
            if y_noise is not None and y_noise.size >= 512:
                cleaned = _nr.reduce_noise(
                    y=audio,
                    y_noise=y_noise,
                    sr=self.rate,
                    stationary=True,
                    n_std_thresh_stationary=n_std,
                    prop_decrease=prop_decrease,
                )
            else:
                # No noise profile available — non-stationary mode
                cleaned = _nr.reduce_noise(
                    y=audio,
                    sr=self.rate,
                    stationary=False,
                    prop_decrease=min(0.55, prop_decrease),
                )
            return np.clip(cleaned, -1.0, 1.0).astype(np.float32)
        except Exception as exc:
            logger.warning("noisereduce failed (%s) — returning raw audio.", exc)
            return audio

    def _generate_static_profile(self) -> np.ndarray:
        """White-noise stand-in for ambient background. Used as last resort."""
        noise = np.random.normal(0, 0.03, int(self.rate * 0.5))
        return noise.astype(np.float32)