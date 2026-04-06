import asyncio
import logging
from core.config import settings
from automation.screen_reader import screen_reader

# Standard library for fuzzy string matching
try:
    from difflib import SequenceMatcher
except ImportError:
    SequenceMatcher = None


class SelectorEngine:
    """
    🎯 The Target Finder.
    Takes fuzzy requests (like "Click the button that says 'Save'") and computes
    the best matching coordinates found by the Screen Reader.
    """

    def __init__(self):
        self.logger = logging.getLogger("SelectorEngine")

    async def get_coordinates_for_text(self, target_text: str, confidence_threshold: float = 0.6) -> dict:
        """
        Scans the screen and finds the center pixel coordinates for a specific word or phrase.
        Zero hardcoding. Uses fuzzy matching to overcome bad OCR or slight typos.
        """
        if not SequenceMatcher:
            self.logger.error("SequenceMatcher missing. Text matching degraded.")
            return {"status": "error", "message": "SequenceMatcher missing"}

        self.logger.info(f"Scanning screen for text target: '{target_text}'")
        
        # 1. Grab all recognized elements from our screen reader
        screen_elements = await screen_reader.get_all_text_with_coordinates()
        
        if not screen_elements:
            return {"status": "error", "message": "No text detected on screen."}

        best_match = None
        highest_score = 0.0

        # 2. Fuzzy match the target text against everything on the screen
        for element in screen_elements:
            found_text = element["text"].lower()
            query_text = target_text.lower()

            # SequenceMatcher gives a score between 0.0 and 1.0 on how similar strings are
            score = SequenceMatcher(None, query_text, found_text).ratio()

            # We also do a simple substring check (e.g., if target is "Save", match "Save File")
            if query_text in found_text:
                score = max(score, 0.85)

            if score > highest_score:
                highest_score = score
                best_match = element

        # 3. Verify if the best match is actually reliable
        if best_match and highest_score >= confidence_threshold:
            self.logger.info(f"🎯 Found target '{target_text}' at ({best_match['center_x']}, {best_match['center_y']}) with score: {highest_score:.2f}")
            return {
                "status": "success",
                "x": best_match["center_x"],
                "y": best_match["center_y"],
                "score": highest_score,
                "text": best_match["text"]
            }

        return {
            "status": "error",
            "message": f"Could not find a reliable match for '{target_text}' on screen."
        }

    def _normalize_score(self, score: float) -> float:
        """Helper to ensure score is bound between 0 and 1."""
        return max(0.0, min(1.0, score))


# Global instance
selector_engine = SelectorEngine()