import os
import numpy as np
import warnings

# Suppress the usual suspects
os.environ['PyTorch_NNPACK_ENABLED'] = '0'
os.environ['TORCH_CPP_LOG_LEVEL'] = 'ERROR'
warnings.filterwarnings('ignore', category=UserWarning)

import noisereduce as nr

class NoiseFilter:
    def __init__(self, rate=16000):
        self.rate = rate
        self.is_calibrated = False
        self.noise_profile = None
        print("🎙️ Noise Filter: Initialized (Spectral Gating)")

    def calibrate(self, background_noise_chunk: np.ndarray):
        """
        Optional: Feed a 1-2 second chunk of pure silence (just room noise) 
        to map the specific frequencies of your room's background noise.
        """
        self.noise_profile = background_noise_chunk
        self.is_calibrated = True
        print("🎙️ Noise Filter: Room profile calibrated.")

    def reduce_noise(self, audio_float32: np.ndarray) -> np.ndarray:
        """
        Applies stationary noise reduction. If no calibration profile exists,
        it automatically guesses the noise profile from the audio itself.
        """
        if self.is_calibrated and self.noise_profile is not None:
            # Use the pre-calibrated room profile (very fast & accurate)
            return nr.reduce_noise(
                y=audio_float32, 
                sr=self.rate, 
                y_noise=self.noise_profile,
                stationary=True
            )
        else:
            # Dynamically calculate noise on the fly
            return nr.reduce_noise(
                y=audio_float32, 
                sr=self.rate, 
                stationary=True,
                n_std_thresh_stationary=1.5 # Lower = more aggressive cleanup
            )