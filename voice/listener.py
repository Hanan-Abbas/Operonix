import collections
import queue
import sys
import pyaudio
import webrtcvad
from voice.stt import SpeechToText
from core.event_bus import bus

class VoiceListener:
    def __init__(self):
        self.vad = webrtcvad.Vad(2) # Aggressiveness from 0 to 3. 2 is a good balance.
        self.audio = pyaudio.PyAudio()
        self.stt = SpeechToText(model_size="base") # Using the upgraded base model!
        
        self.rate = 16000
        self.chunk = 480 # 30ms chunks at 16kHz
        
    