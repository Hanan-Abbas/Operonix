import os
import numpy as np
import warnings
import noisereduce as nr

os.environ['PyTorch_NNPACK_ENABLED'] = '0'
os.environ['TORCH_CPP_LOG_LEVEL'] = 'ERROR'
warnings.filterwarnings('ignore', category=UserWarning)

class NoiseFilter:
    def __init__(self, rate=16000):
        self.rate = rate
        print("🎙️ Noise Filter: Initialized (Spectral Gating)")

    def reduce_noise(self, audio_float32: np.ndarray) -> np.ndarray:
        """
        Applies stationary noise reduction on the WHOLE recorded audio clip.
        This provides much cleaner audio for Whisper.
        """
        # Let noisereduce use its default high-quality windowing
        return nr.reduce_noise(
            y=audio_float32, 
            sr=self.rate, 
            stationary=True,
            n_std_thresh_stationary=1.5
        )