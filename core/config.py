import os
from pathlib import Path
from typing import Dict, List


class Settings:
    """Central configuration for the Operonix AI OS Agent.

    Holds environment variables, model choices, safety thresholds, and file
    paths.
    """

    # --- PROJECT PATHS ---
    # Points to the root "ai_os_agent/" directory
    BASE_DIR: Path = Path(__file__).resolve().parent.parent

    LOGS_DIR: Path = BASE_DIR / "logs"
    SANDBOX_DIR: Path = BASE_DIR / "sandbox"
    PLUGINS_DIR: Path = BASE_DIR / "plugins"

    # --- API KEYS & EXTERNAL SERVICES ---
    # Loaded from environment variables for security
    OPENAI_API_KEY: str = os.getenv("OPENAI_API_KEY", "")
    ANTHROPIC_API_KEY: str = os.getenv("ANTHROPIC_API_KEY", "")

    # --- BRAIN & LLM SETTINGS ---
    # Default models for different specialized tasks
    DEFAULT_LLM: str = "gpt-4o"  # Main decision engine
    FAST_LLM: str = "gpt-4o-mini"  # Quick tasks like intent parsing
    VISION_LLM: str = "gpt-4o"  # Used in automation/vision_model.py

    # --- SYSTEM GUARDRAILS (Crucial for Self-Evolving AI) ---
    MAX_RETRY_ATTEMPTS: int = 3  # For executor/retry_manager.py
    SAFE_MODE: bool = (
        True  # If True, dangerous operations require human confirmation
    )

    # Folders the AI is absolutely NOT allowed to delete or modify
    RESTRICTED_PATHS: List[str] = [
        str(BASE_DIR / "core"),
        str(BASE_DIR / "safety"),
    ]

    # --- UI & FALLBACK DEFAULTS ---
    # Scoring system for tool_selector.py (Prefers API over raw UI clicking)
    TOOL_PRIORITY: Dict[str, int] = {
        "api_tool": 3,
        "shell_tool": 2,
        "ui_tool": 1,
    }

    # --- SERVER & DASHBOARD ---
    API_HOST: str = "127.0.0.1"
    API_PORT: int = 8000

    def __init__(self):
        # Automatically ensure required system directories exist on startup
        for path in [self.LOGS_DIR, self.SANDBOX_DIR]:
            path.mkdir(parents=True, exist_ok=True)


# Global instance to be imported across the project
# e.g., from core.config import settings
settings = Settings()