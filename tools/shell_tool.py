"""
tools/ui_tool.py
─────────────────
Desktop / screen automation tool — UI fallback layer.

Changes from original
──────────────────────
• Added `supported_intents` set.  `can_handle()` is updated to use it
  (old version only matched 6 strings; now fully driven by the set).
• Added `tool_type = "ui_tool"` for priority resolution.
• Everything else is identical to the original.
"""
from __future__ import annotations

import asyncio
import logging
import os
import platform

import pyautogui

from core.config import settings
from core.event_bus import bus

pyautogui.FAILSAFE = True
pyautogui.PAUSE = 0.5


class UITool:
    name = "ui_tool"
    tool_type = "ui_tool"

    supported_intents: set[str] = {
        "click", "double_click", "type_text", "move_cursor",
        "scroll", "navigate", "screenshot",
    }

    def can_handle(self, intent: str) -> bool:
        return intent in self.supported_intents

    def __init__(self) -> None:
        self.os_name = platform.system()
        self.logger = logging.getLogger("UITool")

    async def run(self, action: str, args: dict):
        await bus.emit("ui_op_started", {"action": action, "args": args}, source="ui_tool")

        try:
            if action == "click":
                return await self._click(args.get("x"), args.get("y"), args.get("clicks", 1))
            elif action == "type":
                return await self._type(args.get("text"), args.get("interval", settings.DEFAULT_TYPE_INTERVAL))
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
        return True, "Typed provided text"

    async def _hotkey(self, keys):
        if not keys:
            return False, "No keys provided for hotkey."
        await asyncio.to_thread(pyautogui.hotkey, *keys)
        return True, f"Pressed hotkey: {'+'.join(keys)}"

    async def _move(self, x, y):
        await asyncio.to_thread(pyautogui.moveTo, x, y, duration=0.2)
        return True, f"Moved mouse to ({x}, {y})"

    async def _scroll(self, direction, amount):
        if not direction:
            return False, "Scroll direction not specified."
        scroll_amount = int(amount) if amount else settings.DEFAULT_SCROLL_AMOUNT
        multiplier = 1 if direction in ("up", "right") else -1
        clicks = scroll_amount * multiplier
        if direction in ("left", "right"):
            await asyncio.to_thread(pyautogui.hscroll, clicks)
        else:
            await asyncio.to_thread(pyautogui.scroll, clicks)
        return True, f"Scrolled {direction} by {scroll_amount}"

    async def _screenshot(self, path):
        if not path:
            sandbox_dir = getattr(settings, "SANDBOX_DIR", "sandbox/temp_files")
            os.makedirs(sandbox_dir, exist_ok=True)
            path = os.path.join(sandbox_dir, f"screenshot_{int(asyncio.get_event_loop().time())}.png")
        await asyncio.to_thread(pyautogui.screenshot, path)
        return True, f"Screenshot saved to {path}"


ui_tool = UITool()