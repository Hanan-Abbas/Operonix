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
        
    def listen_for_wake_word(self):
        """Sits in a lightweight loop listening for the trigger phrase."""
        print("👂 Wake Word: Active and listening in background...")
        
        stream = self.audio.open(
            format=pyaudio.paInt16,
            channels=1,
            rate=self.rate,
            input=True,
            frames_per_buffer=self.chunk
        )
        
        try:
            while True:
                # Read chunks of raw audio data from the microphone
                data = stream.read(self.chunk, exception_on_overflow=False)
                
                # Feed the raw bytes directly to openWakeWord
                prediction = self.model.predict(data)
                
                # Check the confidence score of your specific trigger word
                score = prediction.get(self.wake_word, 0)
                
                # Threshold of 0.5 works well as a start. 
                # Raise it to avoid false positives, lower it if it ignores you.
                if score > 0.5:
                    print(f"🔔 Wake Word: Detected '{self.wake_word}' with confidence {score:.2f}!")
                    
                    # Shout over to your event bus that the system needs to wake up!
                    bus.publish("wake_word_detected", {"trigger": self.wake_word})
                    break
                    
        finally:
            stream.stop_stream()
            stream.close()

if __name__ == "__main__":
    detector = WakeWordDetector()
    detector.listen_for_wake_word()