import pyttsx3
from core.event_bus import bus

class TextToSpeech:
    def __init__(self):
        print("🔊 TTS: Initializing voice engine...")
        self.engine = pyttsx3.init()
        
        # Adjust voice rate (speed) and volume
        self.engine.setProperty('rate', 175)  # Default is usually 200, 175 is more natural
        self.engine.setProperty('volume', 1.0) # Max volume
        
        # Optional: You can list and pick different voices (male/female)
        voices = self.engine.getProperty('voices')
        if voices:
            # On Linux, index 0 is usually a male voice, index 1 or others might be female
            self.engine.setProperty('voice', voices[0].id)

    def speak(self, text):
        """Reads text out loud and broadcasts it to the system."""
        if not text:
            return
            
        print(f"🤖 Operonix says: {text}")
        
        # 📢 SHOUT TO THE EVENT BUS (This will let your dashboard update the UI!)
        bus.publish("ai_speaking_started", {"text": text})
        
        self.engine.say(text)
        self.engine.runAndWait()
        
        # 📢 Let the dashboard know the AI is done talking
        bus.publish("ai_speaking_finished", {"status": "idle"})

if __name__ == "__main__":
    tts = TextToSpeech()
    tts.speak("Hello Mohid! My voice system is now online and ready to be connected to your dashboard.")