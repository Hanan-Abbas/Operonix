import asyncio
import logging
import time
from core.config import settings

# We use standard libraries to keep it cross-platform and non-hardcoded
try:
    import pyautogui
    # Fail-safe: Moving the mouse to the upper-left corner will abort execution
    pyautogui.FAILSAFE = True
except ImportError:
    pyautogui = None


class UIFallback:
    """
    🤖 The final fallback layer.
    Simulates keyboard and mouse interactions when APIs or plugins are unavailable.
    Completely application-agnostic.
    """

    def __init__(self):
        self.logger = logging.getLogger("UIFallback")
        self.default_typing_delay = 0.05

        if not pyautogui:
            self.logger.warning("PyAutoGUI not installed! UI Fallback will fail.")

    async def type_text(self, text: str, press_enter: bool = False) -> dict:
        """Types text wherever the cursor is currently focused."""
        if not pyautogui:
            return {"status": "error", "message": "PyAutoGUI missing"}

        try:
            self.logger.info(f"UI Fallback: Typing text (length: {len(text)})")
            
            # Using async sleep to keep the event loop unblocked between characters
            for char in text:
                pyautogui.write(char)
                await asyncio.sleep(self.default_typing_delay)

            if press_enter:
                pyautogui.press('enter')

            return {"status": "success", "message": "Text typed successfully"}
        except Exception as e:
            return {"status": "error", "message": str(e)}

    async def click_element(self, x: int, y: int, double_click: bool = False) -> dict:
        """Clicks on raw pixel coordinates."""
        if not pyautogui:
            return {"status": "error", "message": "PyAutoGUI missing"}

        try:
            self.logger.info(f"UI Fallback: Clicking at coordinates ({x}, {y})")
            
            # Smoothly move the mouse so it feels more natural and less erratic
            pyautogui.moveTo(x, y, duration=0.5)
            
            if double_click:
                pyautogui.doubleClick()
            else:
                pyautogui.click()

            return {"status": "success", "message": f"Clicked at ({x}, {y})"}
        except Exception as e:
            return {"status": "error", "message": str(e)}

    async def press_shortcut(self, keys: list) -> dict:
        """
        Presses a combination of keys (e.g., ['ctrl', 'c'] or ['command', 's']).
        """
        if not pyautogui:
            return {"status": "error", "message": "PyAutoGUI missing"}

        try:
            self.logger.info(f"UI Fallback: Pressing hotkey combination: {keys}")
            pyautogui.hotkey(*keys)
            return {"status": "success", "message": f"Pressed hotkey {keys}"}
        except Exception as e:
            return {"status": "error", "message": str(e)}

    async def get_screen_size(self) -> dict:
        """Returns the current screen resolution."""
        if not pyautogui:
            return {"status": "error", "message": "PyAutoGUI missing"}
            
        width, height = pyautogui.size()
        return {"status": "success", "width": width, "height": height}


# Global instance to be loaded into the tool registry
ui_fallback = UIFallback()