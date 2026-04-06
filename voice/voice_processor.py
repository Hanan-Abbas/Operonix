import asyncio
import logging
import json
from core.event_bus import bus
from core.config import settings
# 🟢 Grab the central client we built earlier
from brain.llm_client import llm_client 

class VoiceProcessor:
    """
    🎙️ The Voice-to-Brain Bridge.
    Takes rough Whisper transcriptions, uses Ollama to correct context/typos,
    maps them against the Vector DB, and triggers the execution pipeline.
    """

    def __init__(self):
        self.logger = logging.getLogger("VoiceProcessor")

    async def start(self):
        """Listen for Whisper transcriptions coming off the event bus."""
        bus.subscribe("whisper_text_transcribed", self.process_voice_command)
        self.logger.info("🎙️ Voice Processor online and waiting for Whisper output...")

    async def process_voice_command(self, event):
        """The main pipeline execution method."""
        task_id = event.data.get("task_id")
        raw_transcript = event.data.get("text")
        
        if not raw_transcript:
            self.logger.warning("Received empty transcript from Whisper.")
            return

        self.logger.info(f"🎙️ Raw Whisper Transcript: '{raw_transcript}'")

        # 1. Ask Ollama to clean up the fuzzy words and infer actual intent
        corrected_text = await self._clean_transcript_with_ollama(raw_transcript)
        self.logger.info(f"✨ Ollama Polished Command: '{corrected_text}'")

        # 2. Push the cleaned command to the parsing layer (which uses your Vector DB)
        # In your previous files, your IntentParser listens for 'request_intent_parsing' 
        # or 'intent_parsed' after vector mapping. We trigger that exact event chain!
        bus.publish(
            "request_intent_parsing",
            data={
                "task_id": task_id,
                "text": corrected_text,
                "context": event.data.get("context", {})
            },
            source="voice_processor"
        )
        
        self.logger.info(f"➡️ Sent polished command to Intent Parser for Vector DB search.")

    async def _clean_transcript_with_ollama(self, transcript: str) -> str:
        """
        🟢 ZERO HARDCODING: Uses the local model to correct Whisper typos based 
        on logical context rather than a fixed dictionary of replacements.
        """
        prompt = f"""
        You are an AI OS Assistant. The user just spoke a command, and the speech-to-text 
        model (Whisper) might have made slight transcription or spelling errors.
        
        Analyze the audio transcript and return a grammatically correct, logical intent command.
        
        Rules:
        1. Fix obvious homophones and phonetic errors (e.g., "commit" instead of "submit" if context calls for it).
        2. Keep it concise.
        3. Do NOT add any pleasantries, conversational filler, or explanations. 
        4. Return ONLY the cleaned, polished text command.
        
        Original Audio Transcript: "{transcript}"
        Cleaned Output:
        """
        
        try:
            # Send to Ollama via the generate method in your llm_client
            response = await llm_client.generate(prompt, use_json=False)
            
            # Fallback to original text if LLM response fails or is empty
            return response.strip() if response else transcript
            
        except Exception as e:
            self.logger.error(f"Failed to polish transcript with Ollama: {e}")
            return transcript


# Global instance
voice_processor = VoiceProcessor()