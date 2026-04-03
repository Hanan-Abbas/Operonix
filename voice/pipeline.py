import os
import sys
import time
from ctypes import *
import numpy as np
import torch
import pyaudio

# Suppress annoying logs
os.environ['PyTorch_NNPACK_ENABLED'] = '0'
os.environ['TORCH_CPP_LOG_LEVEL'] = 'ERROR' 
os.environ['JACK_NO_START_SERVER'] = '1'

import warnings
warnings.filterwarnings('ignore', category=UserWarning)

# Import your custom modules
from voice.wake_word import WakeWordDetector
from voice.stt import SpeechToText
from voice.noise_filter import NoiseFilter
from silero_vad import load_silero_vad
from core.event_bus import bus

class VoicePipeline:
    def __init__(self):
        print("🎙️ Voice Pipeline: Initializing full stack...")
        
        # Load models
        self.wake_detector = WakeWordDetector(wake_word="alexa")
        self.stt = SpeechToText(model_size="tiny")
        self.vad_model = load_silero_vad()
        self.noise_filter = NoiseFilter(rate=16000)
        
        self.audio = pyaudio.PyAudio()
        self.rate = 16000
        self.chunk = 512

    def run(self):
        """The main loop that flips between Wake Word and Listening."""
        while True:
            # --- STEP 1: WAIT FOR WAKE WORD ---
            print("\n💤 Sleeping... Say 'Alexa' to wake me up.")
            self.wake_detector.listen_for_wake_word()
            
            # If the line above finishes, it means a wake word was detected!
            print("🔔 Wake word triggered! Switching to listener...")
            
            # Play a short alert sound or log it (Optional)
            
            # --- STEP 2: LISTEN FOR COMMAND ---
            command_text = self.listen_for_command()
            
            if command_text:
                print(f"📡 Dispatched Command: {command_text}")
                # Broadcast the text to your Orchestrator/Brain!
                bus.publish("user_input_received", {"text": command_text})
            else:
                print("🔇 No clear speech understood. Going back to sleep.")
            
            # Small buffer to prevent instant loop feedback
            time.sleep(1)

    def listen_for_command(self):
        """Listens for the actual command right after the wake word."""
        print("🎤 I'm listening...")
        
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

        try:
            while True:
                data = stream.read(self.chunk, exception_on_overflow=False)
                voiced_frames.append(data)

                # Convert raw bytes to float32 for Silero
                audio_int16 = np.frombuffer(data, dtype=np.int16)
                audio_float32 = audio_int16.astype(np.float32) / 32768.0

                audio_tensor = torch.from_numpy(audio_float32)
                speech_prob = self.vad_model(audio_tensor, self.rate).item()

                if speech_prob > 0.4:
                    if not triggered:
                        print("🔊 Speech detected...")
                        triggered = True
                    silent_chunks = 0
                elif triggered:
                    silent_chunks += 1
                    
                    # Cut off after 80 chunks (~2.5 seconds of silence)
                    if silent_chunks > 80:
                        print("🔇 Silence detected. Processing...")
                        break
                        
        finally:
            stream.stop_stream()
            stream.close()

        if not triggered or len(voiced_frames) < 10:
            return None

        # Combine, filter, and transcribe
        audio_data = b''.join(voiced_frames)
        full_audio_int16 = np.frombuffer(audio_data, dtype=np.int16)
        full_audio_float32 = full_audio_int16.astype(np.float32) / 32768.0
        
        cleaned_audio = self.noise_filter.reduce_noise(full_audio_float32)
        
        cleaned_int16 = (cleaned_audio * 32767.0).astype(np.int16)
        cleaned_bytes = cleaned_int16.tobytes()
        
        return self.stt.transcribe_raw_bytes(cleaned_bytes)

if __name__ == "__main__":
    pipeline = VoicePipeline()
    try:
        pipeline.run()
    except KeyboardInterrupt:
        print("\n🛑 Pipeline stopped.")