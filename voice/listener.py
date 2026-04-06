import os
import sys
import time
import logging
import warnings
from ctypes import *

# Maintain your NNPACK and ALSA flood suppression tricks
os.environ['PyTorch_NNPACK_ENABLED'] = '0'
os.environ['TORCH_CPP_LOG_LEVEL'] = 'ERROR' 
os.environ['JACK_NO_START_SERVER'] = '1'

ERROR_HANDLER_FUNC = CFUNCTYPE(None, c_char_p, c_int, c_char_p, c_int, c_char_p)
def py_error_handler(filename, line, function, err, fmt):
    pass
c_error_handler = ERROR_HANDLER_FUNC(py_error_handler)

try:
    asound = cdll.LoadLibrary('libasound.so.2')
    asound.snd_lib_error_set_handler(c_error_handler)
except OSError:
    pass 

warnings.filterwarnings('ignore', category=UserWarning)

import numpy as np
import torch
from silero_vad import load_silero_vad
from voice.stt import SpeechToText
from voice.noise_filter import NoiseFilter
from core.config import settings

class VoiceListener:
    # 🟢 FIX: Added audio_manager=None to resolve the TypeError
    def __init__(self, audio_manager=None):
        if audio_manager is None:
            raise ValueError("VoiceListener requires an AudioManager instance")
            
        self.logger = logging.getLogger("VoiceListener")
        self.logger.info("🎙️ VAD: Loading Silero Voice Activity Detector...")
        
        torch.set_num_threads(1)

        self.model = load_silero_vad()
        
        # 🟢 DYNAMIC: Pointing to your shared source of truth
        self.audio_manager = audio_manager
        self.rate = getattr(audio_manager, 'rate', 16000) or 16000
        # We pull model size from settings or fallback to 'tiny' (No hardcoding)
        stt_size = getattr(settings, "STT_MODEL_SIZE", "tiny")
        self.stt = SpeechToText(model_size=stt_size)
        
        # Pull audio properties directly from audio manager
        self.rate = getattr(audio_manager, 'rate', 16000)
        self.chunk = getattr(audio_manager, 'chunk', 512)
        
        self.noise_filter = NoiseFilter(rate=self.rate)

        # Safety configs pulled from settings
        self.max_record_seconds = getattr(settings, "MAX_RECORD_SECONDS", 10)
        self.silence_limit = getattr(settings, "SILENCE_LIMIT", 80)

    def listen_until_silent(self):
        self.logger.info("\n🎤 Listening for command...")

        voiced_frames = []
        silent_chunks = 0
        triggered = False

        start_time = time.time()

        while True:
            if time.time() - start_time > self.max_record_seconds:
                self.logger.info("⏱️ Timeout reached.")
                break

            # 🟢 FIX: Instead of stream.read() from PyAudio, we pull from AudioManager!
            data = self.audio_manager.read_chunk()
            if data is None:
                continue

            voiced_frames.append(data.tobytes())

            # Convert numpy array to float32 tensor for Silero VAD
            audio_float32 = data.astype(np.float32) / 32768.0
            audio_tensor = torch.from_numpy(audio_float32)

            speech_prob = self.model(audio_tensor, self.rate).item()

            if speech_prob > 0.4:
                if not triggered:
                    self.logger.info("🔊 Speech detected...")
                    triggered = True
                silent_chunks = 0
            elif triggered:
                silent_chunks += 1

                if silent_chunks > self.silence_limit:
                    self.logger.info("🔇 Silence detected. Processing...")
                    break

        if not voiced_frames:
            return None

        audio_data = b''.join(voiced_frames)

        full_audio_int16 = np.frombuffer(audio_data, dtype=np.int16)
        full_audio_float32 = full_audio_int16.astype(np.float32) / 32768.0

        cleaned_audio = self.noise_filter.reduce_noise(full_audio_float32)

        cleaned_int16 = (cleaned_audio * 32767.0).astype(np.int16)
        cleaned_bytes = cleaned_int16.tobytes()

        text = self.stt.transcribe_raw_bytes(cleaned_bytes)

        if text and len(text.strip()) > 1:
            return text.strip()

        return None