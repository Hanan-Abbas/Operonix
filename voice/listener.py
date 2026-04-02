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

        triggered = False
        ring_buffer = collections.deque(maxlen=10)
        voiced_frames = []

        # Wait for speech, then wait for silence
        while True:
            chunk = stream.read(self.chunk, exception_on_overflow=False)
            is_speech = self.vad.is_speech(chunk, self.rate)

            if not triggered:
                ring_buffer.append((chunk, is_speech))
                num_voiced = len([f for f, speech in ring_buffer if speech])
                if num_voiced > 0.8 * ring_buffer.maxlen:
                    triggered = True
                    print("🔊 Speech detected...")
                    for f, s in ring_buffer:
                        voiced_frames.append(f)
                    ring_buffer.clear()
            else:
                voiced_frames.append(chunk)
                ring_buffer.append((chunk, is_speech))
                num_unvoiced = len([f for f, speech in ring_buffer if not speech])
                
                # If 90% of the last 10 frames are silent, user stopped talking!
                if num_unvoiced > 0.9 * ring_buffer.maxlen:
                    print("🔇 Silence detected. Processing...")
                    break

        stream.stop_stream()
        stream.close()
        
        # Combine frames and send to Whisper
        # (This is where we link to your existing STT logic)
        audio_data = b''.join(voiced_frames)
        
        # Shoot command over to your event bus or print it
        text = self.stt.transcribe_raw_bytes(audio_data) # We can add this small helper to your STT
        return text

if __name__ == "__main__":
    listener = VoiceListener()
    while True:
        command = listener.listen_until_silent()
        print(f"📡 Dispatched Command: {command}")
        
        # Here we would do:
        # bus.publish("user_command_received", {"command": command})