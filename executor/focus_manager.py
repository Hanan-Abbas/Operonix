from __future__ import annotations

import asyncio
import logging
import platform
import shutil

from core.event_bus import bus

logger = logging.getLogger("FocusManager")


class FocusManager:
    def __init__(self) -> None:
        self.os_name = platform.system()

    async def ensure_focus(self, target_title: str, retries: int = 3) -> bool:
        # FIX: bus.emit is a coroutine — must be awaited.
        # Previously bare bus.emit() calls silently discarded coroutine
        # objects, events never fired, and Python emitted RuntimeWarnings.
        await bus.emit("focus_attempt", {"target": target_title})

        for attempt in range(retries):
            success = await self._focus_once(target_title)

            if success:
                await bus.emit("focus_success", {"target": target_title})
                return True

            await asyncio.sleep(0.2)

        await bus.emit("focus_failed", {"target": target_title})
        return False

    async def _focus_once(self, target_title: str) -> bool:
        try:
            if self.os_name == "Windows":
                import win32con
                import win32gui

                def find_window_partial(title: str):
                    matches: list = []
                    def callback(hwnd, _):
                        if title.lower() in win32gui.GetWindowText(hwnd).lower():
                            matches.append(hwnd)
                    win32gui.EnumWindows(callback, None)
                    return matches[0] if matches else None

                hwnd = find_window_partial(target_title)
                if hwnd:
                    if win32gui.IsIconic(hwnd):
                        win32gui.ShowWindow(hwnd, win32con.SW_RESTORE)
                    win32gui.SetForegroundWindow(hwnd)
                    return hwnd == win32gui.GetForegroundWindow()

            elif self.os_name == "Linux":
                if not shutil.which("xdotool"):
                    return False

                proc = await asyncio.create_subprocess_exec(
                    "xdotool", "search", "--name", target_title, "windowactivate",
                    stdout=asyncio.subprocess.PIPE,
                    stderr=asyncio.subprocess.PIPE,
                )
                await proc.wait()
                return proc.returncode == 0

            return False

        except Exception as exc:
            # FIX: error path also needed await
            await bus.emit("focus_error", {"error": str(exc)})
            logger.warning("Focus attempt failed for %r: %s", target_title, exc)
            return False