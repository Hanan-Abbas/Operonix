"""
core/terminal_resolver.py
──────────────────────────
Z-Order Aware Hybrid Execution Model — terminal resolver.

Responsibilities
────────────────
1. Self-window awareness
   On startup, record own PID + walk up the process tree to find the
   terminal window ID that hosts Operonix.  Store it in a blacklist so
   it is never selected as a Bridge target.

2. Temporal focus stack
   Maintain a deque of the last N terminal windows the user actually
   focused.  Updated via a background polling loop (wmctrl / xdotool).

3. CWD matching
   For each candidate terminal, read /proc/<pid>/cwd via os.readlink
   and score against the task's cwd (injected by HotkeyListener /
   panel_controller).

4. Profile selection
   Given a command intent and the scored terminal list, return one of:
     GhostTarget   — silent subprocess, venv pre-activated
     BridgeTarget  — pts injection into a real user terminal
     LabTarget     — spawn a new visible terminal window
     AmbiguousTarget — multiple equal candidates; triggers selection UI

Public API
──────────
    terminal_resolver.init()           — call once at startup (async)
    terminal_resolver.resolve(cwd, intent, profile_hint) -> ResolveResult
"""
from __future__ import annotations

import asyncio
import logging
import os
import re
import shutil
import subprocess
from collections import deque
from dataclasses import dataclass, field
from typing import Deque, List, Optional

log = logging.getLogger("TerminalResolver")

# ── Intent → default profile map ────────────────────────────────────────────
# Commands that modify the calling shell's environment MUST be bridged.
# Heavy / interactive processes go to Lab. Everything else defaults to Ghost.

_FORCE_BRIDGE: frozenset[str] = frozenset({
    "source", "export", "cd", "alias", "unset", "set",
    "activate", "deactivate", "conda", "nvm", "pyenv", "rbenv",
})

_FORCE_LAB: frozenset[str] = frozenset({
    "pytest", "npm run", "yarn dev", "make", "docker compose",
    "docker-compose", "jupyter", "ipython", "python -m",
    "uvicorn", "gunicorn", "flask run", "django", "manage.py",
    "cargo run", "go run", "mvn", "gradle",
})

# Blacklist of window class names that are NOT user terminals
_NON_TERMINAL_CLASSES: frozenset[str] = frozenset({
    "google-chrome", "chromium", "firefox", "code", "code-oss",
    "intellij", "pycharm", "gedit", "nautilus", "thunar",
    "evince", "libreoffice", "gimp", "vlc",
})

# ── Result types ─────────────────────────────────────────────────────────────

@dataclass
class GhostTarget:
    """Silent background subprocess. venv activated if available."""
    venv_path: Optional[str] = None          # path to activate script or None
    cwd: Optional[str] = None

@dataclass
class BridgeTarget:
    """Inject into a real user-facing pseudo-terminal."""
    pts_path: str                             # e.g. /dev/pts/3
    window_id: str
    window_title: str
    cwd: Optional[str] = None

@dataclass
class LabTarget:
    """Spawn an independent visible terminal window."""
    terminal_bin: str                         # e.g. "gnome-terminal"
    cwd: Optional[str] = None

@dataclass
class AmbiguousTarget:
    """Multiple terminals with equal CWD score; needs user selection."""
    candidates: List[BridgeTarget] = field(default_factory=list)

ResolveResult = GhostTarget | BridgeTarget | LabTarget | AmbiguousTarget


# ── Internal terminal record ──────────────────────────────────────────────────

@dataclass
class _TerminalRecord:
    window_id: str
    pid: int
    pts_path: Optional[str]
    cwd: Optional[str]
    title: str
    z_order: int                              # lower = closer to top


# ── Resolver ─────────────────────────────────────────────────────────────────

