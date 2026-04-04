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
from voice.noise_filter import NoiseFilter
from voice.stt import SpeechToText
from voice.wake_word import WakeWordDetector
from voice.audio_manager import AudioManager


class VoicePipeline:
    """🎙️ Full voice stack integrated with AudioManager"""

    def __init__(self):
        print("🎙️ Voice Pipeline: Initializing full stack...")

        # Core components
        self.audio_manager = AudioManager(rate=16000, chunk=512)
        self.wake_detector = WakeWordDetector(wake_word="alexa")
        self.stt = SpeechToText(model_size="tiny")
        self.vad_model = load_silero_vad()
        self.noise_filter = NoiseFilter(rate=16000)

        self.rate = 16000
        self.chunk = 512

    def run(self):
        """Main loop using AudioManager for wake word + command."""
        print("🚀 Voice Pipeline Running...")

        # ✅ Start mic manager and wake word detector
        self.audio_manager.start()
        self.wake_detector.start()

        try:
            while True:
                # --- STEP 1: Detect wake word using shared audio chunks
                audio_chunk = self.audio_manager.read_chunk()
                if audio_chunk is None:
                    time.sleep(0.01)
                    continue

                # Feed chunk to wake word detector
                self.wake_detector.audio_queue.put_nowait(audio_chunk.flatten())
                score = self.wake_detector.detect()

                if score > 0:
                    print("\n🔔 Wake word detected! Switching to command mode...")

                    # Pause wake word detection before listening
                    self.wake_detector.pause()

                    # Clear leftover mic buffer
                    self.audio_manager.clear_buffer()

                    # --- STEP 2: Listen for command
                    command_text = self.listen_for_command()

                    if command_text:
                        print(f"📡 Dispatched Command: {command_text}")
                        bus.publish("user_input_received", {"text": command_text})
                    else:
                        print("🔇 No clear speech understood.")

                    # Small delay to prevent instant retrigger
                    time.sleep(0.5)

                    # Resume wake word detection
                    self.wake_detector.resume()

                time.sleep(0.01)  # 🔥 Prevent CPU overuse

        except KeyboardInterrupt:
            print("\n🛑 Pipeline stopped.")
            self.wake_detector.stop()
            self.audio_manager.stop()

    def listen_for_command(self):
        """Listen for a spoken command using shared AudioManager stream."""
        print("🎤 Listening for command...")
        self.vad_model.reset_states()

        voiced_frames = []
        silent_chunks = 0
        total_chunks = 0
        triggered = False

        while True:
            data = self.audio_manager.read_chunk()
            if data is None:
                time.sleep(0.01)
                continue

            voiced_frames.append(data.copy())
            total_chunks += 1

            # Convert to float32 for VAD
            audio_float32 = data.astype(np.float32).flatten() / 32768.0
            audio_tensor = torch.from_numpy(audio_float32)
            speech_prob = self.vad_model(audio_tensor, self.rate).item()

            if speech_prob > 0.5:
                if not triggered:
                    print("🔊 Speech detected...")
                    triggered = True
                silent_chunks = 0
            elif triggered:
                silent_chunks += 1
                if silent_chunks > 40:  # ~1.2 seconds silence
                    print("🔇 Silence detected. Processing...")
                    break

            # Hard timeout if no speech detected after ~6 seconds
            if not triggered and total_chunks > 200:
                print("⌛ Listening timed out. No speech detected.")
                break

        if not triggered or len(voiced_frames) < 10:
            return None

        # Combine, filter, and transcribe
        full_audio_int16 = np.concatenate(voiced_frames, axis=0).flatten()
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