import platform
import asyncio
import shutil
from core.event_bus import bus

class FocusManager:
    def __init__(self):
        self.os_name = platform.system()

    async def ensure_focus(self, target_title, retries=3):
        bus.emit("focus_attempt", {"target": target_title})

        for attempt in range(retries):
            success = await self._focus_once(target_title)

            if success:
                bus.emit("focus_success", {"target": target_title})
                return True

            await asyncio.sleep(0.2)

        bus.emit("focus_failed", {"target": target_title})
        return False

    async def _focus_once(self, target_title):
        try:
            if self.os_name == "Windows":
                import win32gui, win32con

                def find_window_partial(title):
                    matches = []

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
                    stderr=asyncio.subprocess.PIPE
                )
                await proc.wait()
                return proc.returncode == 0

            return False

        except Exception as e:
            bus.emit("focus_error", {"error": str(e)})
            return False
