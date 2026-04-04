import os
import sys
import time
import warnings
from ctypes import *

# 🛑 1. STOP NNPACK and PyTorch C++ backend spam before anything loads!
os.environ["NNPACK_ENABLED"] = "0"
os.environ["MKLDNN_ENABLED"] = "0"
os.environ["PyTorch_NNPACK_ENABLED"] = "0"
os.environ["TORCH_CPP_LOG_LEVEL"] = "ERROR"
os.environ["JACK_NO_START_SERVER"] = "1"

# Suppress Python's generated UserWarnings (like the CUDA missing warning)
warnings.filterwarnings("ignore", category=UserWarning)

# 🤫 2. Master Mute for lower-level C++ library spam (ALSA/JACK/Torch) on Linux
if sys.platform.startswith("linux"):
    try:
        f = open(os.devnull, "w")
        sys.stderr.flush()
        os.dup2(f.fileno(), sys.stderr.fileno())
    except Exception:
        pass

import numpy as np
# Import libraries after environment variables are set
import sounddevice as sd
import torch
from core.config import settings
from core.event_bus import bus
from silero_vad import load_silero_vad
from voice.noise_filter import NoiseFilter
from voice.stt import SpeechToText
from voice.wake_word import WakeWordDetector


class VoicePipeline:

    def __init__(self):
        print("🎙️ Voice Pipeline: Initializing full stack...")

        # Load models
        self.wake_detector = WakeWordDetector(wake_word="alexa")
        self.stt = SpeechToText(model_size="tiny")
        self.vad_model = load_silero_vad()
        self.noise_filter = NoiseFilter(rate=16000)

        self.rate = 16000
        # Silero VAD is highly optimized for 512 frame chunks at 16kHz (~30ms)
        self.chunk = 512

    def clear_audio_buffer(rate, chunk, device_index):
        try:
            with sd.InputStream(
                samplerate=rate,
                channels=1,
                dtype="int16",
                device=device_index,
                blocksize=chunk,
            ) as stream:
                for _ in range(5):
                    stream.read(chunk)
        except Exception:
            pass

    def run(self):
        """Main loop using non-blocking wake word detection."""

        print("🚀 Voice Pipeline Running...")

        # ✅ Start wake word stream ONCE
        self.wake_detector.start()

        try:
            while True:
                # 🔥 STEP 1: Detect wake word continuously
                score = self.wake_detector.detect()

                if score > 0:
                    print("\n🔔 Wake word detected! Switching to command mode...")

                    # 🛑 VERY IMPORTANT: Stop wake word BEFORE using mic again
                    self.wake_detector.pause()

                    # 🧹 Clear leftover audio buffer
                    self._clear_audio_buffer()

                    # 🎤 STEP 2: Listen for command
                    command_text = self.listen_for_command()

                    if command_text:
                        print(f"📡 Dispatched Command: {command_text}")
                        bus.publish("user_input_received", {"text": command_text})
                    else:
                        print("🔇 No clear speech understood.")

                    # ⏳ Small delay to avoid echo retrigger
                    time.sleep(0.5)

                    # 🔁 Resume wake word detection
                    self.wake_detector.resume()

                time.sleep(0.01)  # 🔥 Prevent CPU overuse

        except KeyboardInterrupt:
            print("\n🛑 Pipeline stopped.")
            self.wake_detector.stop()

    def listen_for_command(self):
        """Listens for the actual command right after the wake word using

        sounddevice.
        """
        print("🎤 I'm listening...")
        self.vad_model.reset_states()
        voiced_frames = []
        silent_chunks = 0
        total_chunks = 0  # 🟢 NEW: Hard timeout counter
        triggered = False

        dev_index = settings.AUDIO_INPUT_INDEX

        try:
            with sd.InputStream(
                samplerate=self.rate,
                channels=1,
                dtype="int16",
                device=dev_index,
                blocksize=self.chunk,
            ) as stream:

                while True:
                    data, overflowed = stream.read(self.chunk)
                    voiced_frames.append(data.copy())
                    total_chunks += 1  # 🟢 NEW: Increment timeout counter

                    audio_float32 = data.astype(np.float32).flatten() / 32768.0
                    audio_tensor = torch.from_numpy(audio_float32)
                    speech_prob = self.vad_model(
                        audio_tensor, self.rate
                    ).item()

                    # 🟢 TWEAK: Lowered to 0.5 so it is easier to trigger over the fan
                    if speech_prob > 0.5:
                        if not triggered:
                            print("🔊 Speech detected...")
                            triggered = True
                        silent_chunks = 0
                    elif triggered:
                        silent_chunks += 1

                        # 🟢 TWEAK: Cut off after 40 chunks (~1.2 seconds) to feel snappier
                        if silent_chunks > 40:
                            print("🔇 Silence detected. Processing...")
                            break

                    # 🟢 NEW: Hard timeout! If it doesn't hear a clear command within ~6 seconds, abort.
                    if not triggered and total_chunks > 200:
                        print("⌛ Listening timed out. No speech detected.")
                        break

        except Exception as e:
            print(f"❌ Error during command recording: {e}")
            return None

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