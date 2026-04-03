import os
import sys
from ctypes import *

os.environ['PyTorch_NNPACK_ENABLED'] = '0'
os.environ['TORCH_CPP_LOG_LEVEL'] = 'ERROR' 
os.environ['JACK_NO_START_SERVER'] = '1'

# Suppress ALSA sound driver flood in the terminal
ERROR_HANDLER_FUNC = CFUNCTYPE(None, c_char_p, c_int, c_char_p, c_int, c_char_p)
def py_error_handler(filename, line, function, err, fmt):
    pass
c_error_handler = ERROR_HANDLER_FUNC(py_error_handler)

try:
    asound = cdll.LoadLibrary('libasound.so.2')
    asound.snd_lib_error_set_handler(c_error_handler)
except OSError:
    pass 

import warnings
warnings.filterwarnings('ignore', category=UserWarning)

import numpy as np
import torch
import pyaudio
from silero_vad import load_silero_vad
from voice.stt import SpeechToText
from voice.noise_filter import NoiseFilter

class VoiceListener:
    def __init__(self):
        print("🎙️ VAD: Loading Silero Voice Activity Detector...")
        torch.set_num_threads(1) 
        
        self.model = load_silero_vad()
        self.audio = pyaudio.PyAudio()
        
        # 🟢 FIX 1: Force Whisper to only expect English so it doesn't guess Thai or Spanish!
        self.stt = SpeechToText(model_size="tiny")
        
        self.noise_filter = NoiseFilter(rate=16000)
        self.rate = 16000
        self.chunk = 512 
        
    def listen_until_silent(self):
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

            # Convert raw bytes to float32 for Silero
            audio_int16 = np.frombuffer(data, dtype=np.int16)
            audio_float32 = audio_int16.astype(np.float32) / 32768.0

            audio_tensor = torch.from_numpy(audio_float32)
            speech_prob = self.model(audio_tensor, self.rate).item()

            # Lowered threshold to 0.4 so it's less aggressive at ignoring you
            if speech_prob > 0.4:
                if not triggered:
                    print("🔊 Speech detected...")
                    triggered = True
                silent_chunks = 0
            elif triggered:
                silent_chunks += 1
                
                # 🟢 FIX 2: Increased from 50 to 80 (~2.5 seconds of silence)
                # This gives you plenty of time to pause mid-sentence without cutoff!
                if silent_chunks > 80:
                    print("🔇 Silence detected. Processing...")
                    break

        stream.stop_stream()
        stream.close()
        
        # Combine all recorded frames
        audio_data = b''.join(voiced_frames)
        
        # Convert full clip to float32
        full_audio_int16 = np.frombuffer(audio_data, dtype=np.int16)
        full_audio_float32 = full_audio_int16.astype(np.float32) / 32768.0
        
        # 🟢 FIX 3: Clean the noise on the FULL audio clip at once!
        cleaned_audio = self.noise_filter.reduce_noise(full_audio_float32)
        
        # Convert back to bytes for Whisper
        cleaned_int16 = (cleaned_audio * 32767.0).astype(np.int16)
        cleaned_bytes = cleaned_int16.tobytes()
        
        text = self.stt.transcribe_raw_bytes(cleaned_bytes)
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