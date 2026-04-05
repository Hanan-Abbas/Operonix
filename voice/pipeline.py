import os
import sys
import time
import warnings
from ctypes import *

# 🛑 Environment fixes for PyTorch & audio
os.environ["NNPACK_ENABLED"] = "0"
os.environ["MKLDNN_ENABLED"] = "0"
os.environ["PyTorch_NNPACK_ENABLED"] = "0"
os.environ["TORCH_CPP_LOG_LEVEL"] = "ERROR"
os.environ["JACK_NO_START_SERVER"] = "1"
warnings.filterwarnings("ignore", category=UserWarning)

if sys.platform.startswith("linux"):
    try:
        f = open(os.devnull, "w")
        sys.stderr.flush()
        os.dup2(f.fileno(), sys.stderr.fileno())
    except Exception:
        pass

import numpy as np
import torch
from core.config import settings
from core.event_bus import bus
from silero_vad import load_silero_vad
from voice.audio_manager import AudioManager
from voice.noise_filter import NoiseFilter
from voice.stt import SpeechToText
from voice.wake_word import WakeWordDetector


class VoicePipeline:
    """🎙️ Full voice stack with Wake Word + Noise Filter"""

    def __init__(self):
        print("🎙️ Voice Pipeline: Initializing full stack...")

        # 🔹 Core components
        self.audio_manager = AudioManager(rate=16000, chunk=512)
        self.noise_filter = NoiseFilter(rate=16000)
        self.vad_model = load_silero_vad()
        self.stt = SpeechToText(model_size="base")
        self.wake_detector = WakeWordDetector(
            wake_word="alexa", audio_manager=self.audio_manager
        )

        # 🟢 State flag to prevent callback deadlocks
        self.is_command_active = False

        # Optional: callback when wake word triggers
        self.wake_detector.set_trigger_callback(self.on_wake_word)

        self.rate = 16000
        self.chunk = 512

    def on_wake_word(self):
        """Callback fired when wake word is detected."""
        print("\n🔔 Wake word callback triggered! Handing off to main loop...")
        self.is_command_active = True

        # 🟢 Aggressively dump 30 chunks (~1 second) to clear your wake word out
        # of the microphone buffer before attempting to listen to the command!
        self.audio_manager.clear_buffer(num_chunks=30)

    def run(self):
        """Main loop using AudioManager for wake word detection."""
        print("🚀 Voice Pipeline Running...")

        # Start shared audio stream
        self.audio_manager.start()

        try:
            while True:
                # 🟢 Check if the callback flag was triggered!
                if self.is_command_active:
                    print("🔄 Swapped to Command Listener state.")
                    # 1. Stop checking for 'Alexa'
                    self.wake_detector.pause()
                    # 2. Clear out the buffered audio
                    self.audio_manager.clear_buffer()

                    # 3. Listen to the user
                    command = self.listen_for_command()

                    if command:
                        print(f"📡 Dispatched Command: {command}")
                        bus.publish("user_input_received", {"text": command})
                    else:
                        print("🔇 No clear speech understood.")

                    # 4. Settle down before turning the mic back on
                    time.sleep(0.5)
                    self.is_command_active = False
                    self.wake_detector.resume()
                    print("\n💤 Going back to sleep. Say 'Alexa'...")

                else:
                    # 🟢 If the callback hasn't fired yet, keep looking for 'Alexa'
                    self.wake_detector.detect()

                time.sleep(0.01)  # prevents maxing out CPU cores

        except KeyboardInterrupt:
            print("\n🛑 Pipeline stopped manually.")
            self.wake_detector.pause()
            self.audio_manager.stop()

    def listen_for_command(self):
        """Listen for a spoken command using AudioManager."""
        print("🎤 Listening for command...")
        self.vad_model.reset_states()
        voiced_frames = []
        silent_chunks = 0
        total_chunks = 0
        triggered = False

        while True:
            chunk = self.audio_manager.read_chunk()
            if chunk is None:
                time.sleep(0.01)
                continue

            voiced_frames.append(chunk.copy())
            total_chunks += 1

            audio_float32 = chunk.astype(np.float32).flatten() / 32768.0
            audio_tensor = torch.from_numpy(audio_float32)
            speech_prob = self.vad_model(audio_tensor, self.rate).item()

            if speech_prob > 0.55:
                if not triggered:
                    print("🔊 Speech detected...")
                    triggered = True
                silent_chunks = 0
            elif triggered:
                silent_chunks += 1
                if silent_chunks > 55:  # ~1.2s silence
                    print("🔇 Silence detected. Processing...")
                    break

            # Timeout if no speech detected (~6s)
            if not triggered and total_chunks > 200:
                print("⌛ Listening timed out. No speech detected.")
                break

        if not triggered or len(voiced_frames) < 10:
            return None

        # 🟢 FIX APPLIED HERE:
        # Combine all frames into one big numpy array
        full_audio_int16 = np.concatenate(voiced_frames, axis=0).flatten()
        full_audio_float32 = full_audio_int16.astype(np.float32) / 32768.0

        # Run through noise cancellation
        cleaned_audio = full_audio_float32
        cleaned_audio = np.clip(cleaned_audio * 1.5, -1.0, 1.0)
        
        import wave
        try:
            # Convert back to int16 just for the wav file
            diag_int16 = (cleaned_audio * 32767.0).astype(np.int16)
            
            with wave.open("test_command.wav", "wb") as wf:
                wf.setnchannels(1)
                wf.setsampwidth(2)  # 2 bytes = 16 bit
                wf.setframerate(self.rate)
                wf.writeframes(diag_int16.tobytes())
            print("💾 DIAGNOSTIC: Saved 'test_command.wav' to your folder!")
        except Exception as e:
            print(f"⚠️ Failed to save debug wav: {e}")


        # Call the new numpy array method directly! Skip turning it back into bytes.
        print("⌛ Transcribing audio...")
        return self.stt.transcribe_numpy_array(cleaned_audio)


if __name__ == "__main__":
    pipeline = VoicePipeline()
    pipeline.run()