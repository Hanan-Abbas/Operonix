"""
context/app_profiler.py

Provides a single public classmethod `AppProfiler.get_active_app_name()`
that returns the name of the currently focused application (e.g. "VS Code",
"Chrome", "Finder").

Used by:
  • core/orchestrator.py  — _emit_app_context_loop polls this every N seconds
  • panel/suggestion_engine.py — ranks plugin strategies per active app

Implementation delegates to context/window_detector.py for the low-level
OS window title, then strips common suffixes to produce a clean app name.
No external state is carried — every call is fresh.
"""
from __future__ import annotations

import logging
import platform
import re
import subprocess
from typing import ClassVar

log = logging.getLogger("AppProfiler")

# ---------------------------------------------------------------------------
# Suffix / noise patterns that appear in window titles but are not app names.
# Extend this list without touching any other module.
# ---------------------------------------------------------------------------
_STRIP_PATTERNS: list[str] = [
    r"\s*[-–|·•]\s*.+$",          # " - VS Code", " | Chrome", " • Slack"
    r"\s*\([^)]+\)$",              # trailing "(workspace)" etc.
    r"\s*\[[^\]]+\]$",             # trailing "[modified]" etc.
]

# Map raw window substrings → canonical app names.
# Add entries here when a new app needs a friendlier label.
_APP_NAME_MAP: dict[str, str] = {
    "visual studio code":  "VS Code",
    "vscode":              "VS Code",
    "google chrome":       "Chrome",
    "mozilla firefox":     "Firefox",
    "safari":              "Safari",
    "microsoft edge":      "Edge",
    "slack":               "Slack",
    "discord":             "Discord",
    "terminal":            "Terminal",
    "iterm":               "iTerm",
    "konsole":             "Terminal",
    "gnome-terminal":      "Terminal",
    "finder":              "Finder",
    "explorer":            "Explorer",
    "nautilus":            "Files",
    "jetbrains":           "JetBrains IDE",
    "pycharm":             "PyCharm",
    "intellij":            "IntelliJ",
    "sublime text":        "Sublime Text",
    "vim":                 "Vim",
    "neovim":              "Neovim",
    "emacs":               "Emacs",
    "obsidian":            "Obsidian",
    "notion":              "Notion",
    "figma":               "Figma",
    "postman":             "Postman",
    "docker":              "Docker",
}


class AppProfiler:
    """
    Stateless helper that identifies the currently focused application.

    All methods are classmethods — instantiation is never required.
    The orchestrator calls `AppProfiler.get_active_app_name()` from an
    asyncio executor thread so it must remain fully synchronous.
    """

    _os: ClassVar[str] = platform.system()

    # ------------------------------------------------------------------
    # Primary public API
    # ------------------------------------------------------------------

    @classmethod
    def get_active_app_name(cls) -> str:
        """
        Return the canonical name of the focused application.
        Returns 'unknown' on any failure so callers never need to handle None.
        """
        try:
            raw_title = cls._get_window_title()
            if not raw_title:
                return "unknown"
            return cls._title_to_app_name(raw_title)
        except Exception as exc:  # noqa: BLE001
            log.debug("AppProfiler: failed to get active app — %s", exc)
            return "unknown"

    @classmethod
    def get_window_title(cls) -> str:
        """Return the raw active window title (for context snapshots)."""
        try:
            return cls._get_window_title() or "Unknown"
        except Exception as exc:  # noqa: BLE001
            log.debug("AppProfiler: window title error — %s", exc)
            return "Unknown"

    # ------------------------------------------------------------------
    # OS-specific window title retrieval
    # ------------------------------------------------------------------

    @classmethod
    def _get_window_title(cls) -> str:
        if cls._os == "Darwin":
            return cls._macos_title()
        if cls._os == "Windows":
            return cls._windows_title()
        return cls._linux_title()

    @classmethod
    def _macos_title(cls) -> str:
        try:
            from AppKit import NSWorkspace  # type: ignore[import]
            app = NSWorkspace.sharedWorkspace().frontmostApplication()
            return app.localizedName() or ""
        except ImportError:
            pass
        # Fallback via AppleScript — works without pyobjc
        try:
            script = 'tell application "System Events" to get name of first process whose frontmost is true'
            result = subprocess.run(
                ["osascript", "-e", script],
                capture_output=True, text=True, timeout=2,
            )
            return result.stdout.strip()
        except Exception:  # noqa: BLE001
            return ""

    @classmethod
    def _windows_title(cls) -> str:
        try:
            import win32gui  # type: ignore[import]
            hwnd = win32gui.GetForegroundWindow()
            return win32gui.GetWindowText(hwnd)
        except ImportError:
            pass
        try:
            # PowerShell fallback
            ps = (
                "Add-Type -TypeDefinition '"
                "using System; using System.Runtime.InteropServices;"
                "public class W { [DllImport(\"user32.dll\")] public static extern IntPtr GetForegroundWindow();"
                " [DllImport(\"user32.dll\")] public static extern int GetWindowText(IntPtr h, System.Text.StringBuilder s, int n);"
                " }'; $h = [W]::GetForegroundWindow(); $s = New-Object System.Text.StringBuilder 256;"
                " [W]::GetWindowText($h, $s, 256) | Out-Null; $s.ToString()"
            )
            result = subprocess.run(
                ["powershell", "-Command", ps],
                capture_output=True, text=True, timeout=3,
            )
            return result.stdout.strip()
        except Exception:  # noqa: BLE001
            return ""

    @classmethod
    def _linux_title(cls) -> str:
        # xdotool (most reliable on X11)
        try:
            result = subprocess.run(
                ["xdotool", "getactivewindow", "getwindowname"],
                capture_output=True, text=True, timeout=2,
            )
            if result.returncode == 0:
                return result.stdout.strip()
        except (FileNotFoundError, subprocess.TimeoutExpired):
            pass

        # ewmh fallback
        try:
            from ewmh import EWMH  # type: ignore[import]
            e = EWMH()
            win = e.getActiveWindow()
            if win:
                name = e.getWMName(win)
                return name.decode("utf-8") if isinstance(name, bytes) else (name or "")
        except Exception:  # noqa: BLE001
            pass

        # wnck fallback (GNOME)
        try:
            import gi  # type: ignore[import]
            gi.require_version("Wnck", "3.0")
            from gi.repository import Wnck  # type: ignore[import]
            screen = Wnck.Screen.get_default()
            screen.force_update()
            win = screen.get_active_window()
            return win.get_name() if win else ""
        except Exception:  # noqa: BLE001
            pass

        return ""

    # ------------------------------------------------------------------
    # Title → canonical app name
    # ------------------------------------------------------------------

    @classmethod
    def _title_to_app_name(cls, title: str) -> str:
        """
        Convert a raw window title like "main.py - VS Code" → "VS Code".
        Resolution order:
          1. Direct substring match against _APP_NAME_MAP
          2. Strip common suffixes, re-check map
          3. Return the stripped title as-is (capitalised)
        """
        lower = title.lower()

        # Direct map hit (fastest path)
        for key, canonical in _APP_NAME_MAP.items():
            if key in lower:
                return canonical

        # Strip trailing cruft and retry
        stripped = title
        for pattern in _STRIP_PATTERNS:
            stripped = re.sub(pattern, "", stripped, flags=re.IGNORECASE).strip()

        lower_stripped = stripped.lower()
        for key, canonical in _APP_NAME_MAP.items():
            if key in lower_stripped:
                return canonical

        # Return what we have, capitalized sensibly
        return stripped.title() if stripped else "unknown"