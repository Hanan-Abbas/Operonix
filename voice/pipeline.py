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

    def run(self):
        """The main loop that flips between Wake Word and Listening."""
        while True:
            # --- 🟢 FIX: PURGE THE LEFTOVER BUFFER ---
            print("\n🧹 Clearing audio buffer...")
            try:
                # We open a stream, pull out whatever is sitting in it, and immediately close it
                with sd.InputStream(
                    samplerate=self.rate,
                    channels=1,
                    dtype="int16",
                    device=settings.AUDIO_INPUT_INDEX,
                    blocksize=self.chunk,
                ) as stream:
                    # Read a few chunks to discard any audio from the previous command
                    for _ in range(5):
                        stream.read(self.chunk)
            except Exception:
                pass  # If it's empty or errors, that's fine!

            # --- STEP 1: WAIT FOR WAKE WORD ---
            print("💤 Sleeping... Say 'Alexa' to wake me up.")
            self.wake_detector.listen_for_wake_word()

            # If the line above finishes, it means a wake word was detected!
            print("🔔 Wake word triggered! Switching to listener...")

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