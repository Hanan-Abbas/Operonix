import asyncio
import logging
import os
import platform
import pyautogui
from core.config import settings
from core.event_bus import bus

# Safety: Moving the mouse to any corner of the screen aborts the script
pyautogui.FAILSAFE = True
# Standard pause between actions to mimic human speed and prevent OS lag
pyautogui.PAUSE = 0.5


class UITool:

    def __init__(self):
        self.name = "ui_tool"
        self.os_name = platform.system()
        self.logger = logging.getLogger("UITool")

    def can_handle(self, intent: str) -> bool:
        """Dynamic check for Executor to know if this tool handles an intent."""
        ui_intents = ["click", "type", "hotkey", "move", "scroll", "screenshot"]
        return intent in ui_intents

    async def run(self, action, args):
        """Main entry point for UI interactions.

        Actions: click, type, move, hotkey, scroll, screenshot
        """
        # Notify the bus for real-time dashboard tracking
        await bus.emit(
            "ui_op_started", {"action": action, "args": args}, source="ui_tool"
        )

        try:
            if action == "click":
                return await self._click(
                    args.get("x"), args.get("y"), args.get("clicks", 1)
                )

            elif action == "type":
                return await self._type(
                    args.get("text"),
                    args.get("interval", settings.DEFAULT_TYPE_INTERVAL),
                )

            elif action == "hotkey":
                return await self._hotkey(args.get("keys", []))

            elif action == "move":
                return await self._move(args.get("x"), args.get("y"))

            elif action == "scroll":
                return await self._scroll(args.get("direction"), args.get("amount"))

            elif action == "screenshot":
                return await self._screenshot(args.get("path"))

            return False, f"Unknown UI action: {action}"

        except Exception as e:
            return False, f"UI Error: {str(e)}"

    async def _click(self, x, y, clicks):
        await asyncio.to_thread(pyautogui.click, x=x, y=y, clicks=clicks)
        return True, f"Clicked at ({x}, {y}) {clicks} times."

    async def _type(self, text, interval):
        if not text:
            return False, "No text provided to type."
        await asyncio.to_thread(pyautogui.write, text, interval=interval)
        return True, f"Typed provided text"

    async def _hotkey(self, keys):
        if not keys:
            return False, "No keys provided for hotkey."
        await asyncio.to_thread(pyautogui.hotkey, *keys)
        return True, f"Pressed hotkey: {'+'.join(keys)}"

    async def _move(self, x, y):
        await asyncio.to_thread(pyautogui.moveTo, x, y, duration=0.2)
        return True, f"Moved mouse to ({x}, {y})"

    async def _scroll(self, direction, amount):
        """Dynamically handles scrolling depending on OS and direction."""
        if not direction:
            return False, "Scroll direction not specified."

        # Convert amount safely or pull from settings
        scroll_amount = (
            int(amount) if amount else settings.DEFAULT_SCROLL_AMOUNT
        )

        # PyAutoGUI uses positive numbers for up/right, negative for down/left
        multiplier = 1 if direction in ("up", "right") else -1
        clicks = scroll_amount * multiplier

        # Handle vertical vs horizontal dynamically
        if direction in ("left", "right"):
            await asyncio.to_thread(pyautogui.hscroll, clicks)
        else:
            await asyncio.to_thread(pyautogui.scroll, clicks)

        return True, f"Scrolled {direction} by {scroll_amount}"

    async def _screenshot(self, path):
        """Saves screenshots to a temporary sandbox directory if no path is given."""
        if not path:
            # Dynamically resolve to a safe temporary space rather than hardcoding a local file
            sandbox_dir = getattr(settings, "SANDBOX_DIR", "sandbox/temp_files")
            os.makedirs(sandbox_dir, exist_ok=True)
            path = os.path.join(sandbox_dir, f"screenshot_{int(asyncio.get_event_loop().time())}.png")

        await asyncio.to_thread(pyautogui.screenshot, path)
        return True, f"Screenshot saved to {path}"


# Global instance
ui_tool = UITool()