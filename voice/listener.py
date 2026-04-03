import os
import sys
from ctypes import *

# Stop NNPACK and Torch C++ flood from firing
os.environ['PyTorch_NNPACK_ENABLED'] = '0'
os.environ['TORCH_CPP_LOG_LEVEL'] = 'ERROR' 

# 🟢 FIX: Suppress ALSA/JACK sound driver flood in the terminal
ERROR_HANDLER_FUNC = CFUNCTYPE(None, c_char_p, c_int, c_char_p, c_int, c_char_p)
def py_error_handler(filename, line, function, err, fmt):
    pass
c_error_handler = ERROR_HANDLER_FUNC(py_error_handler)

try:
    asound = cdll.LoadLibrary('libasound.so.2')
    asound.snd_lib_error_set_handler(c_error_handler)
except OSError:
    pass # Falls back safely if executed on a system without ALSA

import warnings
warnings.filterwarnings('ignore', category=UserWarning)

import numpy as np
import torch
import pyaudio
from silero_vad import load_silero_vad
from voice.stt import SpeechToText
from core.event_bus import bus

class VoiceListener:
    def __init__(self):
        print("🎙️ VAD: Loading Silero Voice Activity Detector...")
        
        torch.set_num_threads(1) 
        
        self.model = load_silero_vad()
        self.audio = pyaudio.PyAudio()
        self.stt = SpeechToText(model_size="tiny")
        
        self.rate = 16000
        # Silero prefers chunks of 512, 1024, or 1536 for 16kHz
        self.chunk = 512 
        
    def listen_until_silent(self):
        """Listens to the mic and stops recording when the user stops talking."""
        print("\n🎤 Voice OS: Listening...")
        
        stream = self.audio.open(
            format=pyaudio.paInt16,
            channels=1,
            rate=self.rate,
            input=True,
            frames_per_buffer=self.chunk
        )

        voiced_frames = []
        silent_chunks = 0
        triggered = False

        while True:
            data = stream.read(self.chunk, exception_on_overflow=False)
            voiced_frames.append(data)

            # Convert raw bytes to float32 numpy array for Silero
            audio_int16 = np.frombuffer(data, dtype=np.int16)
            audio_float32 = audio_int16.astype(np.float32) / 32768.0
            
            # 🟢 NEW: Clean the noise before Silero tries to read it!
            audio_float32 = self.noise_filter.reduce_noise(audio_float32)

            audio_tensor = torch.from_numpy(audio_float32)
 
            speech_prob = self.model(audio_tensor, self.rate).item()

            if speech_prob > 0.5:
                if not triggered:
                    print("🔊 Speech detected...")
                    triggered = True
                silent_chunks = 0
            elif triggered:
                silent_chunks += 1
                
                # 🟢 FIX: Changed from 25 to 50 (~1.6 seconds of silence) 
                # Gives you more time to think without it aggressively cutting off
                if silent_chunks > 50:
                    print("🔇 Silence detected. Processing...")
                    break

        stream.stop_stream()
        stream.close()
        
        # Combine frames and send to Whisper
        audio_data = b''.join(voiced_frames)
        
        text = self.stt.transcribe_raw_bytes(audio_data)
        return text

if __name__ == "__main__":
    listener = VoiceListener()
    try:
        while True:
            command = listener.listen_until_silent()
            if command:
                print(f"📡 Dispatched Command: {command}")
            else:
                print("🔇 No clear speech understood.")
    except KeyboardInterrupt:
        print("\n🛑 Listener stopped.")