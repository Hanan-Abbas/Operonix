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

    