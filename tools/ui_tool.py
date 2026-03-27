import pyautogui
import asyncio
import platform
from core.event_bus import bus

# Safety: Moving the mouse to any corner of the screen aborts the script
pyautogui.FAILSAFE = True
# Standard pause between actions to mimic human speed and prevent OS lag
pyautogui.PAUSE = 0.5

class UITool:
    def __init__(self):
        self.name = "ui_tool"
        self.os_name = platform.system()

    async def run(self, action, args):
        """
        Main entry point for UI interactions.
        Actions: click, type, move, hotkey, scroll, screenshot
        """
        # Notify the bus for real-time dashboard tracking
        await bus.emit("ui_op_started", {"action": action, "args": args}, source="ui_tool")

        try:
            if action == "click":
                return await self._click(args.get("x"), args.get("y"), args.get("clicks", 1))
            
            elif action == "type":
                return await self._type(args.get("text"), args.get("interval", 0.1))
            
            elif action == "hotkey":
                return await self._hotkey(args.get("keys", []))
            
            elif action == "move":
                return await self._move(args.get("x"), args.get("y"))
            
            elif action == "screenshot":
                return await self._screenshot(args.get("path", "screenshot.png"))

            return False, f"Unknown UI action: {action}"

        except Exception as e:
            return False, f"UI Error: {str(e)}"

    async def _click(self, x, y, clicks):
        # Use to_thread to prevent blocking the Event Loop during mouse movement
        await asyncio.to_thread(pyautogui.click, x=x, y=y, clicks=clicks)
        return True, f"Clicked at ({x}, {y}) {clicks} times."

    async def _type(self, text, interval):
        if not text: return False, "No text provided to type."
        await asyncio.to_thread(pyautogui.write, text, interval=interval)
        return True, f"Typed: {text}"

    async def _hotkey(self, keys):
        if not keys: return False, "No keys provided for hotkey."
        await asyncio.to_thread(pyautogui.hotkey, *keys)
        return True, f"Pressed hotkey: {'+'.join(keys)}"

    async def _move(self, x, y):
        await asyncio.to_thread(pyautogui.moveTo, x, y, duration=0.2)
        return True, f"Moved mouse to ({x}, {y})"

    async def _screenshot(self, path):
        # Useful for the Vision Model/Dashboard preview
        await asyncio.to_thread(pyautogui.screenshot, path)
        return True, f"Screenshot saved to {path}"

# Global instance
ui_tool = UITool()