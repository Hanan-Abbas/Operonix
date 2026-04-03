import os
import sys

# Stop NNPACK and PyTorch C++ backend spam before execution
os.environ['PyTorch_NNPACK_ENABLED'] = '0'
os.environ['TORCH_CPP_LOG_LEVEL'] = 'ERROR' 

import warnings
warnings.filterwarnings('ignore', category=UserWarning)

import pyaudio
import numpy as np
import openwakeword
from openwakeword.model import Model
from core.event_bus import bus

class WakeWordDetector:
    def __init__(self, wake_word="alexa"):
        """
        By default, openWakeWord ships with pre-trained models like:
        'alexa', 'hey_mycroft', 'hey_jarvis', 'ok_nabu'
        """
        print(f"👂 Wake Word: Initializing detector for '{wake_word}'...")
        self.wake_word = wake_word
        
        # Download standard models if they don't exist yet
        openwakeword.utils.download_models()
        
        # Instantiate the model
        self.model = Model(wakeword_models=[wake_word])
        self.audio = pyaudio.PyAudio()
        
        # openWakeWord relies on 16kHz audio
        self.rate = 16000
        
        # openWakeWord expects frames in multiples of 80 ms (1280 samples)
        self.chunk = 1280 
        
    