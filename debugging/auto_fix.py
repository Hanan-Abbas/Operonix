import logging
import os
from core.config import settings
from core.event_bus import bus
# We import your brain client to actually communicate with the LLMs!
from brain.llm_client import llm_client

class AutoFixer:
    """🛠️ The self-healing engineer of the AI OS.
    
    Takes a parsed error report, reads the source code, asks the LLM for a 
    solution, and applies the patch.
    """
    def __init__(self):
        self.logger = logging.getLogger("AutoFixer")
        self.max_attempts = 3  # Prevent infinite debug loops!

    