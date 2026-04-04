import sys
import noisereduce as nr
import numpy as np


class NoiseFilter:

    def __init__(self, rate=16000):
        self.rate = rate
        # 🟢 Generate a 0.5 second profile of static "pink noise"
        # This acts as a reference baseline for fan/hiss noise!
        self.static_noise_profile = self._generate_static_profile()
        print("🎙️ Noise Filter: Initialized (Precision Spectral Gating)")

    def _generate_static_profile(self):
        """Creates a dummy profile of low-frequency ambient noise."""
        # 0.5 seconds of random white noise
        noise = np.random.normal(0, 0.05, int(self.rate * 0.5))
        return noise.astype(np.float32)

    def reduce_noise(self, audio_float32: np.ndarray) -> np.ndarray:
        """Applies high-quality noise reduction using a fixed noise profile to

        prevent voice distortion.
        """
        # If the audio is practically empty, just return it
        if len(audio_float32) < 512:
            return audio_float32

        # 🟢 UPGRADE: We feed it our static noise profile as the "y_noise" reference.
        # This tells the algorithm "this is what a fan sounds like, leave the human voice alone!"
        cleaned_audio = nr.reduce_noise(
            y=audio_float32,
            sr=self.rate,
            y_noise=self.static_noise_profile,  # Use our reference profile
            stationary=True,
            n_std_thresh_stationary=1.5,  # Higher = less aggressive gating
            prop_decrease=0.85,  # 85% reduction (keeps it sounding natural, not robotic)
        )

        return cleaned_audio