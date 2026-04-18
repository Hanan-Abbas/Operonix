"""
context/app_classifier.py

Universal App Classifier — integrates with WindowDetector via:
    from context.app_classifier import classifier

Design goals:
  • No hardcoded app lists — works with ANY application, past or future.
  • Two-tier strategy:
      1. Fast local heuristic (regex signal extraction) — runs instantly, zero network cost.
      2. Optional LLM fallback via the Anthropic API for ambiguous titles where
         the heuristic returns LOW confidence.
  • Returns a rich AppContext dataclass consumed by the rest of the pipeline.
"""

from __future__ import annotations

import os
import re
import json
import asyncio
import logging
from dataclasses import dataclass, asdict
from functools import lru_cache
from typing import Optional

logger = logging.getLogger(__name__)


# ──────────────────────────────────────────────────────────────────────────────
# Public data contract
# ──────────────────────────────────────────────────────────────────────────────

@dataclass
class AppContext:
    """Everything the rest of the system needs to know about the active window."""

    # The raw window title that was classified
    raw_title: str

    # A normalised, human-readable app name  e.g. "VS Code", "Slack", "Firefox"
    app_name: str

    # A broad category — deliberately open-ended, not from a fixed enum
    # Examples: "code_editor", "browser", "terminal", "communication",
    #           "document_editor", "spreadsheet", "design_tool", "media_player",
    #           "file_manager", "system_settings", "game", "unknown"
    category: str

    # Free-form sub-context extracted from the title
    # e.g. the currently open file, URL fragment, channel name …
    sub_context: Optional[str]

    # Confidence in the local heuristic result: "high" | "medium" | "low"
    confidence: str

    # True when the LLM was called to resolve an ambiguous title
    llm_used: bool = False

    def to_dict(self) -> dict:
        return asdict(self)


# ──────────────────────────────────────────────────────────────────────────────
# Signal extraction helpers
# ──────────────────────────────────────────────────────────────────────────────

# Patterns that reliably identify categories from *any* window title without
# knowing the app's name in advance.  Order matters — first match wins.
_CATEGORY_SIGNALS: list[tuple[str, str, re.Pattern]] = [
    # (category, description, compiled pattern)

    # ── Code editors / IDEs ───────────────────────────────────────────────────
    ("code_editor", "file extensions common in editors",
     re.compile(r"\.(py|js|ts|jsx|tsx|rs|go|c|cpp|h|hpp|java|kt|swift|rb|php|cs|lua|sh|bash|zsh|fish|toml|yaml|yml|json|xml|html|css|scss|sass|md|mdx|vue|svelte)\b", re.I)),

    ("code_editor", "IDE / editor keyword in title",
     re.compile(r"\b(visual studio|vscode|vs code|intellij|pycharm|webstorm|clion|goland|rider|rubymine|datagrip|android studio|eclipse|netbeans|xcode|sublime text|atom|neovim|nvim|vim|emacs|helix|zed|cursor|windsurf)\b", re.I)),

    # ── Terminals ─────────────────────────────────────────────────────────────
    ("terminal", "shell prompt or terminal keyword",
     re.compile(r"\b(terminal|bash|zsh|fish|sh|powershell|cmd\.exe|command prompt|iTerm|warp|alacritty|kitty|gnome-terminal|konsole|xterm|hyper)\b", re.I)),

    ("terminal", "common shell prompt chars",
     re.compile(r"[$#~]\s*$")),

    # ── Browsers ──────────────────────────────────────────────────────────────
    ("browser", "browser name in title suffix",
     re.compile(r"[-–—]\s*(google chrome|mozilla firefox|safari|microsoft edge|opera|brave|vivaldi|arc|zen browser|chromium|waterfox|librewolf)\s*$", re.I)),

    ("browser", "URL-like content in title",
     re.compile(r"https?://|www\.", re.I)),

    # ── Communication ─────────────────────────────────────────────────────────
    ("communication", "messaging / email app name",
     re.compile(r"\b(slack|discord|teams|zoom|telegram|signal|whatsapp|skype|outlook|thunderbird|mail|gmail|inbox|mattermost|rocket\.chat|element|matrix|messenger)\b", re.I)),

    # ── Document editors ──────────────────────────────────────────────────────
    ("document_editor", "document / word processor signals",
     re.compile(r"\b(word|google docs|libreoffice writer|pages|wordpad|openoffice|notion|obsidian|typora|remarkable|onenote|evernote|bear|logseq)\b", re.I)),

    ("document_editor", "common document file extensions",
     re.compile(r"\.(docx?|odt|rtf|pages|tex|txt|rst|adoc)\b", re.I)),

    # ── Spreadsheets ──────────────────────────────────────────────────────────
    ("spreadsheet", "spreadsheet app or file",
     re.compile(r"\b(excel|google sheets|libreoffice calc|numbers|gnumeric|openoffice calc)\b", re.I)),

    ("spreadsheet", "spreadsheet file extension",
     re.compile(r"\.(xlsx?|ods|csv|tsv)\b", re.I)),

    # ── Design / creative tools ───────────────────────────────────────────────
    ("design_tool", "design app name",
     re.compile(r"\b(figma|sketch|adobe xd|affinity designer|affinity photo|photoshop|illustrator|inkscape|gimp|canva|framer|penpot|krita|blender|cinema 4d|after effects|premiere|davinci resolve|final cut|luminar)\b", re.I)),

    # ── Media / entertainment ─────────────────────────────────────────────────
    ("media_player", "media player signals",
     re.compile(r"\b(vlc|mpv|spotify|apple music|youtube music|deezer|tidal|winamp|media player|quicktime|iina|jellyfin|plex|kodi|netflix|prime video|disney\+|hbo max|youtube)\b", re.I)),

    # ── File managers ─────────────────────────────────────────────────────────
    ("file_manager", "file manager signals",
     re.compile(r"\b(finder|files|nautilus|dolphin|thunar|nemo|konqueror|ranger|yazi|midnight commander|total commander|double commander|windows explorer|file explorer)\b", re.I)),

    # ── System / settings ─────────────────────────────────────────────────────
    ("system_settings", "system tool signals",
     re.compile(r"\b(settings|preferences|system preferences|control panel|task manager|activity monitor|htop|btop|system monitor|regedit|registry|dconf|gnome tweaks)\b", re.I)),

    # ── Databases / data tools ────────────────────────────────────────────────
    ("database_tool", "database GUI or CLI",
     re.compile(r"\b(tableplus|dbeaver|datagrip|pgadmin|mysql workbench|sequel pro|sequel ace|beekeeper studio|sqlite browser|mongodb compass|redis insight|postico)\b", re.I)),

    # ── Note-taking / writing ─────────────────────────────────────────────────
    ("note_taking", "note apps",
     re.compile(r"\b(notion|obsidian|logseq|roam research|bear|standard notes|joplin|zettlr|foam|dendron|anytype)\b", re.I)),

    # ── Games ─────────────────────────────────────────────────────────────────
    ("game", "gaming platform or launcher",
     re.compile(r"\b(steam|epic games|gog|itch\.io|battle\.net|origin|uplay|xbox game pass|lutris|heroic)\b", re.I)),

    # ── Package / project management ──────────────────────────────────────────
    ("project_management", "PM tools",
     re.compile(r"\b(jira|linear|asana|trello|github|gitlab|bitbucket|notion|basecamp|monday\.com|clickup|height)\b", re.I)),
]

# Patterns to extract meaningful sub-context from a window title
_SUB_CONTEXT_PATTERNS: list[re.Pattern] = [
    # "SomeFile.py — VS Code"  →  "SomeFile.py"
    re.compile(r"^(.+?)\s*[-–—]\s*(?:visual studio|vs code|code|pycharm|intellij|sublime|atom|zed|cursor|vim|emacs|neovim)", re.I),
    # "Page Title - Browser"   →  "Page Title"
    re.compile(r"^(.+?)\s*[-–—]\s*(?:google chrome|mozilla firefox|safari|edge|opera|brave|vivaldi|arc|chromium)\s*$", re.I),
    # "Slack | #channel"        →  "#channel"
    re.compile(r"(?:slack|discord|teams)\s*[|:]\s*(.+)", re.I),
    # Generic "Something - AppName" → "Something"
    re.compile(r"^(.+?)\s*[-–—]\s*\S+\s*$"),
]


def _extract_sub_context(title: str) -> Optional[str]:
    for pat in _SUB_CONTEXT_PATTERNS:
        m = pat.match(title)
        if m:
            candidate = m.group(1).strip()
            # Skip if the candidate is basically the whole title
            if candidate and len(candidate) < len(title) - 2:
                return candidate
    return None


def _extract_app_name(title: str) -> str:
    """
    Best-effort: grab the trailing segment after a dash/pipe separator,
    which most apps use for their own name.
    """
    for sep in (" — ", " – ", " - ", " | "):
        parts = title.rsplit(sep, 1)
        if len(parts) == 2:
            candidate = parts[-1].strip()
            # Reasonable app name: 2-50 chars, no newlines
            if 2 <= len(candidate) <= 50 and "\n" not in candidate:
                return candidate
    # Fallback: first word (capitalised)
    return title.split()[0].capitalize() if title.split() else "Unknown"


def _local_classify(title: str) -> tuple[str, str, str, str]:
    """
    Returns (app_name, category, sub_context|None, confidence).
    Confidence is "high" when multiple signals agree, "medium" for one match,
    "low" when nothing fired.
    """
    title_clean = title.strip()
    if not title_clean or title_clean in ("Unknown", "Unknown Linux Window", "Unknown Mac Window"):
        return ("Unknown", "unknown", None, "low")

    hits: dict[str, int] = {}  # category → hit count

    for category, _desc, pattern in _CATEGORY_SIGNALS:
        if pattern.search(title_clean):
            hits[category] = hits.get(category, 0) + 1

    sub_context = _extract_sub_context(title_clean)
    app_name = _extract_app_name(title_clean)

    if not hits:
        return (app_name, "unknown", sub_context, "low")

    # Pick the category with the most signal hits
    best_category = max(hits, key=hits.__getitem__)
    confidence = "high" if hits[best_category] >= 2 else "medium"

    return (app_name, best_category, sub_context, confidence)


# ──────────────────────────────────────────────────────────────────────────────
# LLM fallback (async, only called for low-confidence results)
# ──────────────────────────────────────────────────────────────────────────────

_LLM_SYSTEM_PROMPT = """\
You are an expert desktop-application classifier.
Given a raw window title from any operating system, return a JSON object with:
  - "app_name":    short, human-readable name for the application (string)
  - "category":    snake_case category such as code_editor, browser, terminal,
                   communication, document_editor, spreadsheet, design_tool,
                   media_player, file_manager, system_settings, database_tool,
                   note_taking, game, project_management, or invent an accurate
                   one if none fit — do NOT use "unknown" if you can infer it.
  - "sub_context": brief description of what the user is viewing/doing inside
                   the app, or null if not determinable.

Return ONLY valid JSON — no markdown, no explanation."""


async def _llm_classify(title: str) -> Optional[tuple[str, str, Optional[str]]]:
    """
    Calls the Anthropic API asynchronously.
    Returns (app_name, category, sub_context) or None on failure.
    Requires ANTHROPIC_API_KEY in environment.
    """
    api_key = os.environ.get("ANTHROPIC_API_KEY")
    if not api_key:
        return None

    try:
        import anthropic  # optional dependency
    except ImportError:
        logger.debug("anthropic package not installed; LLM fallback disabled.")
        return None

    try:
        client = anthropic.AsyncAnthropic(api_key=api_key)
        message = await client.messages.create(
            model="claude-haiku-4-5-20251001",   # fast + cheap
            max_tokens=256,
            system=_LLM_SYSTEM_PROMPT,
            messages=[{"role": "user", "content": f'Window title: "{title}"'}],
        )
        raw = message.content[0].text.strip()
        data = json.loads(raw)
        return (
            str(data.get("app_name", "Unknown")),
            str(data.get("category", "unknown")),
            data.get("sub_context"),  # may be None
        )
    except Exception as exc:
        logger.warning("LLM classify failed for %r: %s", title, exc)
        return None


# ──────────────────────────────────────────────────────────────────────────────
# Cache — avoids re-classifying the same title within the same session
# ──────────────────────────────────────────────────────────────────────────────

_cache: dict[str, AppContext] = {}
_llm_lock: asyncio.Lock | None = None   # initialised lazily in async context


def _get_llm_lock() -> asyncio.Lock:
    global _llm_lock
    if _llm_lock is None:
        _llm_lock = asyncio.Lock()
    return _llm_lock


# ──────────────────────────────────────────────────────────────────────────────
# Public API — synchronous wrapper (used by WindowDetector)
# ──────────────────────────────────────────────────────────────────────────────

class AppClassifier:
    """
    Drop-in singleton used by WindowDetector.

    Sync path (called in the hot loop):
        ctx = classifier.classify(title)       → AppContext (fast, heuristic only)

    Async path (called when you want LLM resolution for low-confidence results):
        ctx = await classifier.classify_async(title)
    """

    # ── Synchronous classify (heuristic only, always instant) ─────────────────
    def classify(self, title: str) -> AppContext:
        if title in _cache:
            return _cache[title]

        app_name, category, sub_context, confidence = _local_classify(title)
        ctx = AppContext(
            raw_title=title,
            app_name=app_name,
            category=category,
            sub_context=sub_context,
            confidence=confidence,
            llm_used=False,
        )
        _cache[title] = ctx
        return ctx

    # ── Async classify (heuristic + optional LLM fallback) ────────────────────
    async def classify_async(self, title: str) -> AppContext:
        # Return cached result immediately
        if title in _cache:
            cached = _cache[title]
            # Re-resolve if low confidence and LLM hasn't run yet
            if cached.confidence != "low" or cached.llm_used:
                return cached

        app_name, category, sub_context, confidence = _local_classify(title)

        llm_used = False
        if confidence == "low":
            async with _get_llm_lock():
                # Double-check cache after acquiring lock
                if title in _cache and (_cache[title].llm_used or _cache[title].confidence != "low"):
                    return _cache[title]

                result = await _llm_classify(title)
                if result:
                    app_name, category, sub_context = result
                    confidence = "medium"   # LLM result — trusted but not perfect
                    llm_used = True

        ctx = AppContext(
            raw_title=title,
            app_name=app_name,
            category=category,
            sub_context=sub_context,
            confidence=confidence,
            llm_used=llm_used,
        )
        _cache[title] = ctx
        return ctx

    def clear_cache(self) -> None:
        """Flush the in-memory title cache (e.g. for testing)."""
        _cache.clear()

    def cache_size(self) -> int:
        return len(_cache)


# Singleton instance — imported by window_detector.py
classifier = AppClassifier()