"""
voice/noise_filter.py — Operonix AI OS Agent
══════════════════════════════════════════════
Backward-compatibility shim.

All noise-reduction logic now lives in voice/deep_noise_filter.py which
supports both DeepFilterNet (neural) and noisereduce (spectral gating).

Existing code that imports `from voice.noise_filter import NoiseFilter`
continues to work unchanged — it just gets the upgraded implementation.
"""
from voice.deep_noise_filter import NoiseFilter  # noqa: F401  re-export

__all__ = ["NoiseFilter"]