import os
import queue
import sys
import numpy as np
import openwakeword
import sounddevice as sd
from openwakeword.model import Model
from core.config import settings
from core.event_bus import bus


class WakeWordDetector:

    def __init__(self, wake_word="alexa"):
        """Initializes the wake word detector using sounddevice instead of

        PyAudio.
        """
        print(f"👂 Wake Word: Initializing detector for '{wake_word}'...")
        self.wake_word = wake_word

        # Instantiate the model (auto-downloads if needed)
        self.model = Model()
        self.rate = 16000

        # Thread-safe queue to pass audio from the mic to the model
        self.audio_queue = queue.Queue()

    def _audio_callback(self, indata, frames, time, status):
        """This function is called by sounddevice in the background for every

        audio chunk.
        """
        if status:
            print(f"⚠️ Audio status: {status}", file=sys.stderr)

        # 🟢 FIX 1: Flatten the array for EVERY chunk, not just when there's an error!
        self.audio_queue.put(indata.copy().flatten())

    def listen_for_wake_word(self):
        """Sits in a lightweight loop listening for the trigger phrase."""
        print("👂 Wake Word: Active and listening in background...")

        # Let the OS grab the default microphone automatically
        dev_index = settings.AUDIO_INPUT_INDEX

        # Start a smooth, background audio stream
        with sd.InputStream(
            samplerate=self.rate,
            channels=1,
            dtype="int16",
            device=dev_index,
            callback=self._audio_callback,
            blocksize=1280,  # openWakeWord's ideal chunk size
        ):
            while True:
                # Get the next chunk from the queue
                audio_chunk = self.audio_queue.get()

                # 🟢 FIX 2: Convert the numpy array to a pure Python list
                audio_list = audio_chunk.tolist()

                # Feed the raw list directly to openWakeWord
                prediction = self.model.predict(audio_list)

                # Check the confidence score of your specific trigger word
                score = prediction.get(self.wake_word, 0)
                print(f"Debug Score: {score:.4f}", end="\r")

                # If the score is decent, trigger the event
                if score > 0.85:
                    print(
                        f"\n🔔 Wake Word: Detected '{self.wake_word}' with confidence {score:.2f}!"
                    )

                    # Hand off to the thread-safe EventBus
                    bus.publish(
                        "wake_word_detected", {"trigger": self.wake_word}
                    )
                    break


if __name__ == "__main__":
    detector = WakeWordDetector()
    detector.listen_for_wake_word()