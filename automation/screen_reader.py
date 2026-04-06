import asyncio
import logging
import os
from core.config import settings
from core.event_bus import bus

# We use standard libraries to keep it cross-platform
try:
    import pyautogui
    from PIL import Image
    import pytesseract  # Standard open-source OCR
except ImportError:
    pyautogui = None
    Image = None
    pytesseract = None


class ScreenReader:
    """
    👁️ The "Eyes" of the fallback system.
    Takes screenshots and extracts text via OCR to find UI elements without hardcoding.
    """

    def __init__(self):
        self.logger = logging.getLogger("ScreenReader")
        
        # Point to Tesseract if the path is in settings (common on Windows)
        tesseract_cmd = getattr(settings, "TESSERACT_CMD", None)
        if tesseract_cmd and pytesseract:
            pytesseract.pytesseract.tesseract_cmd = tesseract_cmd

    async def capture_screen(self, region=None) -> Image.Image:
        """Takes a screenshot of the whole screen or a specific region."""
        if not pyautogui:
            self.logger.error("PyAutoGUI not installed.")
            return None
            
        # Running synchronous screenshot in a separate thread to avoid blocking the event loop
        screenshot = await asyncio.to_thread(pyautogui.screenshot, region=region)
        return screenshot

    async def get_all_text_with_coordinates(self) -> list:
        """
        Scans the screen and returns a list of found text blocks with their pixel coordinates.
        This allows the agent to find words like 'File', 'Save', or 'Submit' on any app.
        """
        if not pytesseract:
            self.logger.error("PyTesseract not installed.")
            return []

        screenshot = await self.capture_screen()
        if not screenshot:
            return []

        try:
            # Use Tesseract to get detailed data including box coordinates
            # Running this heavy CPU bound task in an executor thread
            data = await asyncio.to_thread(
                pytesseract.image_to_data, screenshot, output_type=pytesseract.Output.DICT
            )
            
            elements = []
            n_boxes = len(data['text'])
            
            for i in range(n_boxes):
                # Only keep blocks that actually contain text (filtering out confidence <= 0)
                if int(data['conf'][i]) > 40 and data['text'][i].strip():
                    elements.append({
                        "text": data['text'][i],
                        "x": data['left'][i],
                        "y": data['top'][i],
                        "width": data['width'][i],
                        "height": data['height'][i],
                        # Calculate the center of the word for easy clicking!
                        "center_x": data['left'][i] + (data['width'][i] // 2),
                        "center_y": data['top'][i] + (data['height'][i] // 2)
                    })
                    
            return elements

        except Exception as e:
            self.logger.error(f"Failed to read screen text: {e}")
            return []


# Global instance
screen_reader = ScreenReader()