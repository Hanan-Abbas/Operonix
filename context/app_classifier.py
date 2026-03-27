from core.event_bus import bus

class AppClassifier:
    def __init__(self):
        # Keywords to identify app categories
        self.categories = {
            "editor": ["visual studio code", "vscode", "sublime", "notepad++", "pycharm"],
            "browser": ["chrome", "firefox", "edge", "safari", "brave"],
            "terminal": ["terminal", "powershell", "cmd", "iterm", "bash"],
            "communication": ["slack", "discord", "teams", "zoom"]
        }

    async def start(self):
        # Listen for the detector's raw output
        bus.subscribe("raw_context_detected", self.classify)
        print("🔍 App Classifier: Ready to identify active applications.")

    async def classify(self, event):
        raw_title = event.data.get("window_title", "").lower()
        task_id = event.data.get("task_id")
        
        identified_type = "generic_ui"
        
        for category, keywords in self.categories.items():
            if any(key in raw_title for key in keywords):
                identified_type = category
                break
        
        # Add the classification to the data packet
        event.data["app_type"] = identified_type
        
        # Pass it to the next stage: State Extraction
        await bus.emit("app_classified", event.data, source="app_classifier")

app_classifier = AppClassifier()