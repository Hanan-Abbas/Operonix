"""
panel/panel_theme.py

Resolves the active theme name to a ThemeTokens dataclass.
All rendering code reads ONLY from ThemeTokens — no hex values
or sizes ever appear elsewhere.

Built-in themes are defined here as plain dicts.
Users can register arbitrary custom token dicts via PanelState.
The 'auto' theme detects the OS dark/light preference at runtime.
"""
from __future__ import annotations

import logging
import platform
import subprocess
from dataclasses import dataclass
from typing import Any

log = logging.getLogger(__name__)


# ---------------------------------------------------------------------------
# Token schema
# ---------------------------------------------------------------------------

@dataclass(frozen=True)
class ThemeTokens:
    """Complete set of design tokens consumed by the panel renderer."""

    # --- core palette ---
    bg_primary: str
    bg_secondary: str
    bg_tertiary: str
    border_color: str
    accent: str
    accent_hover: str
    accent_text: str        # text on accent background

    # --- text ---
    text_primary: str
    text_secondary: str
    text_muted: str
    text_code: str

    # --- state colours ---
    success: str
    warning: str
    error: str
    info: str

    # --- method-tag badge colours ---
    tag_plugin: str
    tag_api: str
    tag_command: str
    tag_shell: str
    tag_ui: str

    # --- typography ---
    font_family: str
    font_size_base: int     # pt
    font_size_sm: int
    font_size_lg: int

    # --- geometry ---
    radius_sm: int          # px
    radius_md: int
    radius_lg: int
    spacing_unit: int       # base spacing in px

    # --- misc ---
    scrollbar_color: str
    selection_bg: str
    shadow: str             # CSS-style box-shadow string for Qt stylesheet


# ---------------------------------------------------------------------------
# Built-in theme definitions
# ---------------------------------------------------------------------------

def _base_geometry() -> dict[str, Any]:
    """Shared geometry tokens across all themes."""
    return dict(
        font_family="JetBrains Mono, Fira Code, Consolas, monospace",
        font_size_base=13,
        font_size_sm=11,
        font_size_lg=15,
        radius_sm=4,
        radius_md=8,
        radius_lg=14,
        spacing_unit=8,
    )


_BUILTIN_DEFS: dict[str, dict[str, Any]] = {

    "dark": dict(
        bg_primary="#1a1a1f",
        bg_secondary="#25252d",
        bg_tertiary="#2e2e38",
        border_color="#3a3a48",
        accent="#6c8ef7",
        accent_hover="#8aaaf9",
        accent_text="#ffffff",
        text_primary="#e8e8f0",
        text_secondary="#a0a0b8",
        text_muted="#5a5a72",
        text_code="#c5d3f7",
        success="#4ade80",
        warning="#fbbf24",
        error="#f87171",
        info="#60a5fa",
        tag_plugin="#7c3aed",   # violet  — plugin ecosystem
        tag_api="#0891b2",      # cyan    — network / API calls
        tag_command="#059669",  # emerald — terminal commands
        tag_shell="#E8A838",    # amber   — shell execution
        tag_ui="#d97706",       # orange  — UI automation
        scrollbar_color="#3a3a48",
        selection_bg="#6c8ef730",
        shadow="0 8px 32px rgba(0,0,0,0.6)",
        **_base_geometry(),
    ),

    "light": dict(
        bg_primary="#f5f5f7",
        bg_secondary="#ffffff",
        bg_tertiary="#ebebef",
        border_color="#d0d0dc",
        accent="#4a6cf7",
        accent_hover="#3555e8",
        accent_text="#ffffff",
        text_primary="#1a1a2e",
        text_secondary="#4a4a68",
        text_muted="#9090a8",
        text_code="#2d4fd4",
        success="#16a34a",
        warning="#d97706",
        error="#dc2626",
        info="#2563eb",
        tag_plugin="#7c3aed",   # violet
        tag_api="#0e7490",      # dark cyan
        tag_command="#065f46",  # dark emerald
        tag_shell="#b45309",    # dark amber — readable on light bg
        tag_ui="#92400e",       # dark orange
        scrollbar_color="#c8c8d8",
        selection_bg="#4a6cf720",
        shadow="0 8px 32px rgba(0,0,0,0.15)",
        **_base_geometry(),
    ),

    "midnight": dict(
        bg_primary="#080e1f",
        bg_secondary="#0d1630",
        bg_tertiary="#152045",
        border_color="#1e3060",
        accent="#3b82f6",
        accent_hover="#60a5fa",
        accent_text="#ffffff",
        text_primary="#ccd9ff",
        text_secondary="#7a96d4",
        text_muted="#3d5080",
        text_code="#93c5fd",
        success="#34d399",
        warning="#fcd34d",
        error="#f87171",
        info="#38bdf8",
        tag_plugin="#8b5cf6",   # purple
        tag_api="#06b6d4",      # cyan
        tag_command="#10b981",  # teal
        tag_shell="#f59e0b",    # amber
        tag_ui="#f59e0b",       # same amber family, slightly distinct via label
        scrollbar_color="#1e3060",
        selection_bg="#3b82f630",
        shadow="0 8px 40px rgba(0,0,80,0.7)",
        **_base_geometry(),
    ),

    "solarized_dark": dict(
        bg_primary="#002b36",
        bg_secondary="#073642",
        bg_tertiary="#0d4052",
        border_color="#1a5263",
        accent="#268bd2",
        accent_hover="#2aa198",
        accent_text="#fdf6e3",
        text_primary="#839496",
        text_secondary="#657b83",
        text_muted="#4a6674",
        text_code="#93a1a1",
        success="#859900",
        warning="#b58900",
        error="#dc322f",
        info="#268bd2",
        tag_plugin="#6c71c4",   # violet
        tag_api="#2aa198",      # cyan
        tag_command="#859900",  # green
        tag_shell="#b58900",    # yellow — solarized yellow
        tag_ui="#cb4b16",       # orange
        scrollbar_color="#1a5263",
        selection_bg="#268bd230",
        shadow="0 8px 32px rgba(0,0,0,0.7)",
        **_base_geometry(),
    ),

    "solarized_light": dict(
        bg_primary="#fdf6e3",
        bg_secondary="#eee8d5",
        bg_tertiary="#e6dfc8",
        border_color="#d3cbb6",
        accent="#268bd2",
        accent_hover="#2aa198",
        accent_text="#fdf6e3",
        text_primary="#657b83",
        text_secondary="#839496",
        text_muted="#b8b0a0",
        text_code="#586e75",
        success="#859900",
        warning="#b58900",
        error="#dc322f",
        info="#268bd2",
        tag_plugin="#6c71c4",   # violet
        tag_api="#2aa198",      # cyan
        tag_command="#859900",  # green
        tag_shell="#b58900",    # yellow
        tag_ui="#cb4b16",       # orange
        scrollbar_color="#c8c0ac",
        selection_bg="#268bd220",
        shadow="0 8px 32px rgba(0,0,0,0.15)",
        **_base_geometry(),
    ),

    "dracula": dict(
        bg_primary="#282a36",
        bg_secondary="#1e2029",
        bg_tertiary="#343746",
        border_color="#44475a",
        accent="#bd93f9",
        accent_hover="#caa9fa",
        accent_text="#282a36",
        text_primary="#f8f8f2",
        text_secondary="#b0b8c8",
        text_muted="#6272a4",
        text_code="#8be9fd",
        success="#50fa7b",
        warning="#f1fa8c",
        error="#ff5555",
        info="#8be9fd",
        tag_plugin="#bd93f9",   # purple
        tag_api="#8be9fd",      # cyan
        tag_command="#50fa7b",  # green
        tag_shell="#f1fa8c",    # yellow — dracula yellow
        tag_ui="#ffb86c",       # orange
        scrollbar_color="#44475a",
        selection_bg="#bd93f930",
        shadow="0 8px 32px rgba(0,0,0,0.7)",
        **_base_geometry(),
    ),

    "nord": dict(
        bg_primary="#2e3440",
        bg_secondary="#3b4252",
        bg_tertiary="#434c5e",
        border_color="#4c566a",
        accent="#88c0d0",
        accent_hover="#8fbcbb",
        accent_text="#2e3440",
        text_primary="#eceff4",
        text_secondary="#d8dee9",
        text_muted="#7a8696",
        text_code="#81a1c1",
        success="#a3be8c",
        warning="#ebcb8b",
        error="#bf616a",
        info="#88c0d0",
        tag_plugin="#b48ead",   # mauve
        tag_api="#88c0d0",      # frost blue
        tag_command="#a3be8c",  # aurora green
        tag_shell="#ebcb8b",    # aurora yellow
        tag_ui="#d08770",       # aurora orange
        scrollbar_color="#4c566a",
        selection_bg="#88c0d030",
        shadow="0 8px 32px rgba(0,0,0,0.6)",
        **_base_geometry(),
    ),

    "gruvbox": dict(
        bg_primary="#282828",
        bg_secondary="#3c3836",
        bg_tertiary="#504945",
        border_color="#665c54",
        accent="#d79921",
        accent_hover="#fabd2f",
        accent_text="#282828",
        text_primary="#ebdbb2",
        text_secondary="#d5c4a1",
        text_muted="#928374",
        text_code="#8ec07c",
        success="#b8bb26",
        warning="#fabd2f",
        error="#fb4934",
        info="#83a598",
        tag_plugin="#d3869b",   # pink
        tag_api="#83a598",      # aqua
        tag_command="#b8bb26",  # yellow-green
        tag_shell="#fabd2f",    # bright yellow
        tag_ui="#fe8019",       # orange
        scrollbar_color="#665c54",
        selection_bg="#d7992130",
        shadow="0 8px 32px rgba(0,0,0,0.7)",
        **_base_geometry(),
    ),

    "monokai": dict(
        bg_primary="#272822",
        bg_secondary="#1e1f1c",
        bg_tertiary="#3e3d32",
        border_color="#49483e",
        accent="#a6e22e",
        accent_hover="#c4f050",
        accent_text="#272822",
        text_primary="#f8f8f2",
        text_secondary="#cfcfc2",
        text_muted="#75715e",
        text_code="#66d9e8",
        success="#a6e22e",
        warning="#e6db74",
        error="#f92672",
        info="#66d9e8",
        tag_plugin="#ae81ff",   # purple
        tag_api="#66d9e8",      # cyan
        tag_command="#a6e22e",  # green
        tag_shell="#e6db74",    # yellow
        tag_ui="#fd971f",       # orange
        scrollbar_color="#49483e",
        selection_bg="#a6e22e30",
        shadow="0 8px 32px rgba(0,0,0,0.7)",
        **_base_geometry(),
    ),

    "catppuccin_mocha": dict(
        bg_primary="#1e1e2e",
        bg_secondary="#181825",
        bg_tertiary="#313244",
        border_color="#45475a",
        accent="#cba6f7",
        accent_hover="#d4baff",
        accent_text="#1e1e2e",
        text_primary="#cdd6f4",
        text_secondary="#bac2de",
        text_muted="#585b70",
        text_code="#89dceb",
        success="#a6e3a1",
        warning="#f9e2af",
        error="#f38ba8",
        info="#89dceb",
        tag_plugin="#cba6f7",   # mauve
        tag_api="#89dceb",      # sky
        tag_command="#a6e3a1",  # green
        tag_shell="#f9e2af",    # yellow
        tag_ui="#fab387",       # peach
        scrollbar_color="#45475a",
        selection_bg="#cba6f730",
        shadow="0 8px 32px rgba(0,0,0,0.7)",
        **_base_geometry(),
    ),

    "catppuccin_latte": dict(
        bg_primary="#eff1f5",
        bg_secondary="#e6e9ef",
        bg_tertiary="#dce0e8",
        border_color="#ccd0da",
        accent="#8839ef",
        accent_hover="#7127d4",
        accent_text="#eff1f5",
        text_primary="#4c4f69",
        text_secondary="#6c6f85",
        text_muted="#9ca0b0",
        text_code="#179299",
        success="#40a02b",
        warning="#df8e1d",
        error="#d20f39",
        info="#179299",
        tag_plugin="#8839ef",   # mauve
        tag_api="#179299",      # teal
        tag_command="#40a02b",  # green
        tag_shell="#df8e1d",    # yellow
        tag_ui="#fe640b",       # peach
        scrollbar_color="#ccd0da",
        selection_bg="#8839ef20",
        shadow="0 8px 32px rgba(0,0,0,0.15)",
        **_base_geometry(),
    ),
}

# 'auto' is resolved at runtime; map it to the base dark fallback until resolved.
_BUILTIN_DEFS["auto"] = _BUILTIN_DEFS["dark"]


# ---------------------------------------------------------------------------
# OS dark-mode detection
# ---------------------------------------------------------------------------

def _os_prefers_dark() -> bool:
    """Best-effort detection of OS dark-mode preference."""
    system = platform.system()
    try:
        if system == "Darwin":
            result = subprocess.run(
                ["defaults", "read", "-g", "AppleInterfaceStyle"],
                capture_output=True, text=True, timeout=2,
            )
            return result.stdout.strip().lower() == "dark"
        if system == "Windows":
            import winreg  # type: ignore[import]
            key = winreg.OpenKey(
                winreg.HKEY_CURRENT_USER,
                r"Software\Microsoft\Windows\CurrentVersion\Themes\Personalize",
            )
            value, _ = winreg.QueryValueEx(key, "AppsUseLightTheme")
            return value == 0
        if system == "Linux":
            result = subprocess.run(
                ["gsettings", "get", "org.gnome.desktop.interface", "gtk-theme"],
                capture_output=True, text=True, timeout=2,
            )
            return "dark" in result.stdout.lower()
    except Exception:  # noqa: BLE001
        pass
    return True  # safe fallback


# ---------------------------------------------------------------------------
# Public resolver
# ---------------------------------------------------------------------------

def resolve_theme(
    theme_name: str,
    custom_themes: dict[str, dict[str, Any]] | None = None,
    font_family: str | None = None,
    font_size: int | None = None,
) -> ThemeTokens:
    """
    Resolve *theme_name* to a fully-populated ThemeTokens instance.

    Resolution order:
      1. 'auto'  → detects OS preference, then falls to 'dark' or 'light'
      2. built-in themes
      3. custom user-registered themes
      4. fallback to 'dark'
    """
    custom_themes = custom_themes or {}
    name = theme_name

    if name == "auto":
        name = "dark" if _os_prefers_dark() else "light"

    if name in _BUILTIN_DEFS:
        tokens = dict(_BUILTIN_DEFS[name])
    elif name in custom_themes:
        # Merge custom tokens over the dark base so missing keys still work.
        tokens = {**_BUILTIN_DEFS["dark"], **custom_themes[name]}
    else:
        log.warning("panel_theme: unknown theme '%s', falling back to 'dark'.", name)
        tokens = dict(_BUILTIN_DEFS["dark"])

    # Allow per-session font overrides from PanelState.
    if font_family:
        tokens["font_family"] = font_family
    if font_size:
        tokens["font_size_base"] = font_size
        tokens["font_size_sm"] = max(9, font_size - 2)
        tokens["font_size_lg"] = font_size + 2

    return ThemeTokens(**tokens)