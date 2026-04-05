import os
import json
from core.event_bus import bus
import ollama  # Make sure to run: pip install ollama

class IntentHandler:
    """🧠 Uses Ollama to understand messy speech and take action"""
    
    def __init__(self):
        print("🧠 Intent Handler: Active and listening for commands...")
        bus.subscribe("user_input_received", self.process_intent)
        
        # We define a strict schema to force Ollama to return exactly what we need
        self.intent_schema = {
            "type": "object",
            "properties": {
                "intent": {
                    "type": "string",
                    "enum": ["create_file", "search_knowledge", "unknown"],
                    "description": "What the user wants to do. create_file is for making files. search_knowledge is for asking questions."
                },
                "file_name": {
                    "type": "string", 
                    "description": "The name of the file to create, if applicable."
                },
                "search_query": {
                    "type": "string",
                    "description": "The actual question or topic to search in the vector DB."
                }
            },
            "required": ["intent"]
        }

    def process_intent(self, data):
        raw_text = data.get("text", "")
        print(f"🎤 Raw Whisper Speech: '{raw_text}'")

        try:
            # Pass the text to Ollama with the enforced schema
            # Tip: Use a fast model like 'llama3.2' or 'qwen2.5'
            response = ollama.chat(
                model='llama3.2', 
                messages=[{
                    'role': 'user', 
                    'content': f"Extract the intent from this spoken audio: {raw_text}"
                }],
                format=self.intent_schema,
                options={'temperature': 0} # 0 means highly accurate and strictly bound to the schema
            )
            
            # Parse the structured JSON response
            structured_data = json.loads(response['message']['content'])
            print(f"🤖 Ollama Decoded Intent: {structured_data}")
            
            self.execute_operation(structured_data)

        except Exception as e:
            print(f"⚠️ Ollama Intent extraction failed: {e}")

    def execute_operation(self, data):
        intent = data.get("intent")
        
        # Action 1: Create a File
        if intent == "create_file":
            file_name = data.get("file_name", "untitled.txt")
            if not file_name.endswith(".txt"):
                file_name += ".txt"
                
            desktop_path = os.path.expanduser("~/Desktop")
            full_path = os.path.join(desktop_path, file_name)
            
            with open(full_path, "w") as f:
                f.write("# File created by Operonix Voice Agent")
            print(f"📁 SUCCESS: Created file at {full_path}!")
            
        # Action 2: Talk to Vector DB
        elif intent == "search_knowledge":
            query = data.get("search_query")
            print(f"🔍 Querying Vector DB for: '{query}'")
            # Here you would trigger your existing Vector DB search!
            # results = your_vector_db.search(query)
            
        else:
            print("🤷 Intent unrecognized or user just making conversation.")