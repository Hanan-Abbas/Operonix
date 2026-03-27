from core.event_bus import bus

class AppClassifier:
    def __init__(self):
        # Dictionary mapping keywords to App Types
        self.rules = {
            "browser": ["chrome", "firefox", "brave", "edge", "opera"],
            "code_editor": ["visual studio code", "cursor", "pycharm", "vim", "nano"],
            "terminal": ["terminal", "bash", "cmd", "powershell", "ubuntu"],
            "document": ["pdf", "word", "excel", "acrobat", "document"],
            "communication": ["whatsapp", "slack", "discord", "teams", "telegram"],
            "media": ["spotify", "vlc", "youtube"]
        }

    def classify(self, window_title: str) -> str:
        """Analyzes the window title to determine the app type."""
        if not window_title or window_title == "Unknown Linux Window":
            return "Background Process"

        title_lower = window_title.lower()

        for app_type, keywords in self.rules.items():
            for keyword in keywords:
                if keyword in title_lower:
                    # Format nicely (e.g., "code_editor" -> "Code Editor")
                    return app_type.replace("_", " ").title()
        
        return "General App"

# Global instance
classifier = AppClassifier()