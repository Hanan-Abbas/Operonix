import sys
import numpy as np
import pyaudio
from silero_vad import load_silero_vad, read_audio, get_speech_timestamps
from voice.stt import SpeechToText
from core.event_bus import bus

class VoiceListener:
    def __init__(self):
        print("🎙️ VAD: Loading Silero Voice Activity Detector...")
        self.model = load_silero_vad()
        self.audio = pyaudio.PyAudio()
        self.stt = SpeechToText(model_size="base")
        
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

            # Get speech probability (returns a float between 0 and 1)
            # We wrap the chunk in a list because Silero expects a batch
            speech_prob = self.model(audio_float32, self.rate).item()

            if speech_prob > 0.5:
                if not triggered:
                    print("🔊 Speech detected...")
                    triggered = True
                silent_chunks = 0
            elif triggered:
                silent_chunks += 1
                
                # If silent for about 1 second (16000 samples / 512 chunk size = ~31 chunks)
                if silent_chunks > 25:
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