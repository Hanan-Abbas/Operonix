import os
import io
import wave
import numpy as np
import pyaudio
from faster_whisper import WhisperModel
from core.config import settings

# Suppress annoying background spam before initializing
os.environ['PyTorch_NNPACK_ENABLED'] = '0'
os.environ['JACK_NO_START_SERVER'] = '1'

class SpeechToText:
    def __init__(self, model_size="base"):
        """
        Initializes the local Whisper model.
        Model sizes: 'tiny', 'base', 'small', 'medium'
        'tiny' is the fastest and perfect for short command parsing!
        """
        print(f"🎙️ STT: Loading Faster-Whisper model ({model_size})...")
        
        # Running on CPU. Change device to "cuda" if you have a dedicated Nvidia GPU.
        # compute_type="int8" keeps RAM usage low and inference fast.
        self.model = WhisperModel(model_size, device="cpu", compute_type="int8")
        print("🎙️ STT: Model loaded successfully.")
        
        # Audio recording settings
        self.format = pyaudio.paInt16
        self.channels = 1
        self.rate = 16000
        self.chunk = 1024
        self.audio = pyaudio.PyAudio()

        # Keep decoding tunable from settings to avoid rigid behavior.
        self.beam_size = int(getattr(settings, "STT_BEAM_SIZE", 5))
        self.best_of = int(getattr(settings, "STT_BEST_OF", 5))
        self.temperature = float(getattr(settings, "STT_TEMPERATURE", 0.0))
        self.language = getattr(settings, "STT_LANGUAGE", "en")

    def _build_initial_prompt(self):
        """
        Bias decoding toward command-like vocabulary using dynamic capabilities.
        """
        try:
            from capabilities.registry import capability_registry
            intents = capability_registry.get_all_names()
        except Exception:
            intents = []

        if not intents:
            return None

        hint_words = []
        for intent in intents[:64]:
            hint_words.extend(intent.replace("_", " ").split())

        unique_words = []
        seen = set()
        for word in hint_words:
            w = word.strip().lower()
            if w and w not in seen:
                seen.add(w)
                unique_words.append(w)

        if not unique_words:
            return None
        return "voice command vocabulary: " + ", ".join(unique_words[:80])

    def _transcribe_audio(self, audio_np, return_metadata: bool = False):
        initial_prompt = self._build_initial_prompt()
        segments_iter, info = self.model.transcribe(
            audio_np,
            beam_size=self.beam_size,
            best_of=self.best_of,
            temperature=self.temperature,
            language=self.language,
            condition_on_previous_text=False,
            vad_filter=True,
            initial_prompt=initial_prompt,
        )
        segments = list(segments_iter)
        text = " ".join([segment.text.strip() for segment in segments if segment.text]).strip()

        if not return_metadata:
            return text

        # Confidence signals (model-provided, not hardcoded mappings).
        avg_logprobs = [getattr(s, "avg_logprob", None) for s in segments]
        avg_logprobs = [x for x in avg_logprobs if isinstance(x, (int, float))]
        mean_avg_logprob = (sum(avg_logprobs) / len(avg_logprobs)) if avg_logprobs else None

        no_speech_probs = [getattr(s, "no_speech_prob", None) for s in segments]
        no_speech_probs = [x for x in no_speech_probs if isinstance(x, (int, float))]
        mean_no_speech_prob = (sum(no_speech_probs) / len(no_speech_probs)) if no_speech_probs else None

        meta = {
            "language": getattr(info, "language", None),
            "duration": getattr(info, "duration", None),
            "mean_avg_logprob": mean_avg_logprob,
            "mean_no_speech_prob": mean_no_speech_prob,
            "segments": len(segments),
        }
        return text, meta

    def estimate_confidence(self, meta: dict) -> float:
        """
        Produces a 0..1 confidence score using model-provided metadata.
        This is a heuristic over logprob + no_speech probability, not a hardcoded phrase map.
        """
        if not isinstance(meta, dict):
            return 0.0

        mean_avg_logprob = meta.get("mean_avg_logprob")
        mean_no_speech_prob = meta.get("mean_no_speech_prob")
        segments = meta.get("segments")

        # If we got no segments, it's likely garbage/silence.
        if not isinstance(segments, int) or segments <= 0:
            return 0.0

        # avg_logprob is usually negative; closer to 0 is better.
        if isinstance(mean_avg_logprob, (int, float)):
            # Map [-2.0, -0.2] roughly into [0, 1]
            lp = float(mean_avg_logprob)
            lp_score = (lp + 2.0) / 1.8
        else:
            lp_score = 0.4

        # no_speech_prob: higher means more likely silence; invert it.
        if isinstance(mean_no_speech_prob, (int, float)):
            ns = float(mean_no_speech_prob)
            ns_score = 1.0 - max(0.0, min(1.0, ns))
        else:
            ns_score = 0.6

        score = (0.65 * lp_score) + (0.35 * ns_score)
        return max(0.0, min(1.0, score))

    def listen_and_transcribe(self, duration=5):
        """Records audio from the microphone and returns the transcribed text."""
        print(f"\n🎤 Listening for {duration} seconds...")
        
        stream = self.audio.open(
            format=self.format,
            channels=self.channels,
            rate=self.rate,
            input=True,
            frames_per_buffer=self.chunk
        )
              
        frames = []
        for _ in range(0, int(self.rate / self.chunk * duration)):
            data = stream.read(self.chunk)
            frames.append(data)
            
        print("⌛ Processing audio...")
        stream.stop_stream()
        stream.close()
        
        # Combine bytes
        audio_data = b''.join(frames)
        
        # Transcribe directly using the raw byte handler!
        return self.transcribe_raw_bytes(audio_data)

    def transcribe_raw_bytes(self, audio_data, return_metadata: bool = False):
        """
        🟢 UPGRADED: Accepts raw audio bytes and transcribes them.
        Skips in-memory WAV creation for pure speed!
        """
        if not audio_data:
            return ""

        # Convert the raw 16-bit PCM bytes directly into a float32 NumPy array
        # This is exactly what faster-whisper expects.
        audio_np = np.frombuffer(audio_data, dtype=np.int16).astype(np.float32) / 32768.0
        
        return self._transcribe_audio(audio_np, return_metadata=return_metadata)

    def transcribe_numpy_array(self, audio_np, return_metadata: bool = False):
        """
        🟢 HIGH-SPEED UPGRADE: Accepts a direct float32 numpy array.
        Zero conversion overhead!
        """
        if audio_np is None or len(audio_np) == 0:
            return ""
            
        # Guarantee it's float32 for Faster-Whisper
        audio_np = audio_np.astype(np.float32)
        
        return self._transcribe_audio(audio_np, return_metadata=return_metadata)
        
# Simple test execution
if __name__ == "__main__":
    stt = SpeechToText(model_size="tiny")
    try:
        while True:
            text = stt.listen_and_transcribe(duration=4)
            if text:
                print(f"🗣️ You said: {text}")
            else:
                print("🔇 No speech detected.")
    except KeyboardInterrupt:
        print("\n🛑 STT stopped.")