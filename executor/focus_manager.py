"""
executor/focus_manager.py
──────────────────────────
Window focus manager — extended for Gap 3 (JIT UI readiness).

Changes from original
──────────────────────
Original: ensure_focus(target_title, retries) → bool
  Tries to bring a window to the foreground via OS APIs (xdotool on Linux,
  win32gui on Windows).  All original logic is preserved unchanged.

Added for the plan:

  ensure_focus_for_ui(decision) → bool
    Called by UIReadinessGuard inside executor.py when FocusDriftError is
    raised after the JIT focus check fails.  Accepts a MethodDecision so
    it reads expected_app from the frozen decision rather than requiring
    the caller to extract it.

    Guarantees:
      • Only ONE re-focus attempt is made (plan §4.3 Gap 3 fix).
      • If the attempt fails, the caller (executor) aborts the UI step
        and tags it ENV_TRANSIENT — the learner never sees it.
      • FocusDriftError and UIStateMismatchError from a second JIT check
        are both caught by the executor, not by this manager.

  app_name_to_window_title(app_name) → str
    Heuristic that converts an AppContext.app_name ("vscode", "chrome", …)
    to the window title substring used by ensure_focus().  Reads a mapping
    from settings.APP_NAME_TO_TITLE_MAP (dict) with a built-in fallback
    so no values are hardcoded here.
"""
from __future__ import annotations

import asyncio
import logging
import platform
import shutil
from typing import TYPE_CHECKING

from core.config import settings
from core.event_bus import bus

if TYPE_CHECKING:
    from tools.routing_decision import MethodDecision

logger = logging.getLogger("FocusManager")


# ─────────────────────────────────────────────────────────────────────────────
# App name → window title heuristic
# ─────────────────────────────────────────────────────────────────────────────

def app_name_to_window_title(app_name: str) -> str:
    """
    Convert an AppContext.app_name identifier to a window title substring
    that OS window-search APIs can match against.

    Primary source: settings.APP_NAME_TO_TITLE_MAP (dict[str, str]) — set
    via dynamic_settings.json so operators can extend it without code changes.

    Built-in fallback covers the most common app names seen in the wild.
    The fallback is a last resort — it can never be exhaustive.
    """
    from_settings: dict[str, str] = getattr(
        settings, "APP_NAME_TO_TITLE_MAP", {}
    ) or {}
    if app_name in from_settings:
        return from_settings[app_name]

    # Built-in fallback — derived from AppClassifier category names
    _BUILTIN: dict[str, str] = {
        "vscode"      : "Visual Studio Code",
        "code"        : "Visual Studio Code",
        "chrome"      : "Google Chrome",
        "firefox"     : "Firefox",
        "safari"      : "Safari",
        "terminal"    : "Terminal",
        "iterm"       : "iTerm2",
        "gnome-terminal": "Terminal",
        "konsole"     : "Konsole",
        "notepad"     : "Notepad",
        "sublime"     : "Sublime Text",
        "pycharm"     : "PyCharm",
        "intellij"    : "IntelliJ",
        "slack"       : "Slack",
        "discord"     : "Discord",
        "spotify"     : "Spotify",
        "finder"      : "Finder",
        "explorer"    : "File Explorer",
        "nautilus"    : "Files",
    }
    if app_name.lower() in _BUILTIN:
        return _BUILTIN[app_name.lower()]

    # Last resort: use the app_name itself — OS title search is fuzzy
    return app_name


# ─────────────────────────────────────────────────────────────────────────────
# FocusManager
# ─────────────────────────────────────────────────────────────────────────────

class FocusManager:
    """
    Brings a named window to the foreground via OS-specific APIs.

    Public interface
    ────────────────
    await ensure_focus(target_title, retries) → bool
        Original method — unchanged.  Used by the legacy executor path and
        by the orchestrator's focus pre-flight check.

    await ensure_focus_for_ui(decision) → bool
        New method for Gap 3.  Called by the executor's UI dispatch path
        when UIReadinessGuard raises FocusDriftError.  Makes exactly ONE
        re-focus attempt then returns; the executor decides what to do next.
    """

    def __init__(self) -> None:
        self.os_name = platform.system()

    # ── Original method — preserved verbatim ─────────────────────────────────

    async def ensure_focus(self, target_title: str, retries: int = 3) -> bool:
        """
        Attempt to bring *target_title* window to the foreground.

        Returns True if focus was successfully acquired, False otherwise.
        Retries up to *retries* times with 200 ms between attempts.
        """
        await bus.emit("focus_attempt", {"target": target_title})

        for attempt in range(retries):
            success = await self._focus_once(target_title)
            if success:
                await bus.emit("focus_success", {"target": target_title})
                return True
            await asyncio.sleep(0.2)

        await bus.emit("focus_failed", {"target": target_title})
        return False

    # ── New method for Gap 3 ──────────────────────────────────────────────────

    async def ensure_focus_for_ui(self, decision: "MethodDecision") -> bool:
        """
        Make exactly ONE re-focus attempt for the expected app in *decision*.

        Called by the executor after UIReadinessGuard raises FocusDriftError.
        The plan mandates a single attempt — this method does NOT loop.

        Returns True if focus was acquired, False otherwise.  The executor
        tags a False return as ENV_TRANSIENT and aborts the UI step cleanly.
        """
        expected_app: str | None = decision.expected_app
        if not expected_app:
            logger.debug(
                "ensure_focus_for_ui: no expected_app in decision — skip."
            )
            return False

        window_title = app_name_to_window_title(expected_app)

        bus.publish(
            "ui_refocus_attempt",
            {
                "expected_app" : expected_app,
                "window_title" : window_title,
                "method"       : decision.method.value,
            },
            source="focus_manager",
        )
        logger.info(
            "UI re-focus: one attempt for app='%s' title='%s'",
            expected_app, window_title,
        )

        success = await self._focus_once(window_title)

        bus.publish(
            "ui_refocus_result",
            {
                "expected_app" : expected_app,
                "window_title" : window_title,
                "success"      : success,
            },
            source="focus_manager",
        )

        if success:
            logger.info(
                "UI re-focus succeeded for app='%s'.", expected_app
            )
        else:
            logger.warning(
                "UI re-focus FAILED for app='%s' — executor will abort UI step.",
                expected_app,
            )

        return success

    # ── Internal OS dispatch — preserved verbatim ─────────────────────────────

    async def _focus_once(self, target_title: str) -> bool:
        """Single focus attempt via OS-specific API."""
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

            elif self.os_name == "Darwin":
                proc = await asyncio.create_subprocess_exec(
                    "osascript", "-e",
                    f'tell application "{target_title}" to activate',
                    stdout=asyncio.subprocess.PIPE,
                    stderr=asyncio.subprocess.PIPE,
                )
                await proc.wait()
                return proc.returncode == 0

            return False

        except Exception as exc:
            await bus.emit("focus_error", {"error": str(exc)})
            logger.warning("Focus attempt failed for %r: %s", target_title, exc)
            return False