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

class TerminalResolver:
    """
    Singleton that owns self-window awareness and terminal discovery.
    Call init() once at startup; then call resolve() per command.
    """

    def __init__(self) -> None:
        self._own_window_ids: set[str] = set()
        self._own_pids: set[int] = set()
        self._focus_stack: Deque[str] = deque(maxlen=10)  # window_ids, newest first
        self._terminal_bin: Optional[str] = None
        self._poll_task: Optional[asyncio.Task] = None
        self._initialized: bool = False

    # ── Startup ──────────────────────────────────────────────────────────────

    async def init(self) -> None:
        """
        Must be called once at startup from an async context.

        1. Find and blacklist our own terminal window.
        2. Detect the best available terminal emulator binary.
        3. Start the background focus-stack polling loop.
        """
        if self._initialized:
            return
        self._initialized = True

        # Step 1 — self-window awareness
        await asyncio.get_running_loop().run_in_executor(None, self._discover_own_window)

        # Step 2 — detect terminal binary
        self._terminal_bin = self._detect_terminal_bin()
        log.info("TerminalResolver: terminal binary = %s", self._terminal_bin)

        # Step 3 — start polling loop
        self._poll_task = asyncio.create_task(self._poll_focus_loop())
        log.info(
            "TerminalResolver: initialized (own window IDs = %s)",
            self._own_window_ids,
        )

    def _discover_own_window(self) -> None:
        """
        Walk up the process tree from our PID to find the terminal window
        that is hosting Operonix, then blacklist it.
        """
        own_pid = os.getpid()
        ancestor_pids: set[int] = {own_pid}

        # Collect all ancestor PIDs up to init (pid=1)
        pid = own_pid
        for _ in range(20):
            try:
                with open(f"/proc/{pid}/status") as f:
                    for line in f:
                        if line.startswith("PPid:"):
                            ppid = int(line.split()[1])
                            if ppid <= 1:
                                break
                            ancestor_pids.add(ppid)
                            pid = ppid
                            break
            except OSError:
                break

        self._own_pids = ancestor_pids

        # Map ancestor PIDs to X11 window IDs via wmctrl
        if not shutil.which("wmctrl"):
            log.warning("wmctrl not found — self-window blacklist may be incomplete")
            return

        try:
            out = subprocess.check_output(
                ["wmctrl", "-l", "-p"], text=True, timeout=3.0
            )
            for line in out.splitlines():
                parts = line.split(None, 4)
                if len(parts) < 4:
                    continue
                win_id = parts[0]
                try:
                    win_pid = int(parts[2])
                except ValueError:
                    continue
                if win_pid in ancestor_pids:
                    self._own_window_ids.add(win_id)
                    log.debug("Blacklisted own window: %s (pid=%d)", win_id, win_pid)
        except Exception as exc:
            log.warning("_discover_own_window: wmctrl failed — %s", exc)

    @staticmethod
    def _detect_terminal_bin() -> str:
        """Return the first available terminal emulator binary."""
        candidates = [
            "gnome-terminal", "xterm", "konsole",
            "xfce4-terminal", "lxterminal", "tilix", "alacritty", "kitty",
        ]
        for bin_ in candidates:
            if shutil.which(bin_):
                return bin_
        return "xterm"  # last-resort fallback

    # ── Focus-stack polling ───────────────────────────────────────────────────

    async def _poll_focus_loop(self) -> None:
        """
        Every 0.5 s, query the active window.  If it is a terminal (and not
        our own window), push its window ID onto the temporal focus stack.
        """
        while True:
            try:
                await asyncio.sleep(0.5)
                win_id = await asyncio.get_running_loop().run_in_executor(
                    None, self._get_active_window_id
                )
                if win_id and win_id not in self._own_window_ids:
                    # Only push if it looks like a terminal
                    wm_class = await asyncio.get_running_loop().run_in_executor(
                        None, self._get_wm_class, win_id
                    )
                    if self._is_terminal_class(wm_class):
                        if not self._focus_stack or self._focus_stack[0] != win_id:
                            self._focus_stack.appendleft(win_id)
            except asyncio.CancelledError:
                break
            except Exception as exc:
                log.debug("_poll_focus_loop: %s", exc)

    @staticmethod
    def _get_active_window_id() -> Optional[str]:
        if shutil.which("xdotool"):
            try:
                return subprocess.check_output(
                    ["xdotool", "getactivewindow"], text=True, timeout=1.0
                ).strip()
            except Exception:
                pass
        if shutil.which("wmctrl"):
            try:
                out = subprocess.check_output(
                    ["wmctrl", "-l", "-p"], text=True, timeout=1.0
                )
                # First line is usually the topmost window
                first = out.strip().splitlines()
                if first:
                    return first[0].split()[0]
            except Exception:
                pass
        return None

    @staticmethod
    def _get_wm_class(win_id: str) -> str:
        if shutil.which("xprop"):
            try:
                out = subprocess.check_output(
                    ["xprop", "-id", win_id, "WM_CLASS"], text=True, timeout=1.0
                )
                return out.lower()
            except Exception:
                pass
        return ""

    @staticmethod
    def _is_terminal_class(wm_class: str) -> bool:
        terminal_keywords = (
            "terminal", "konsole", "xterm", "alacritty",
            "kitty", "tilix", "gnome-terminal", "urxvt", "rxvt",
            "lxterminal", "xfce4-terminal",
        )
        return any(kw in wm_class for kw in terminal_keywords)

    # ── Terminal discovery ────────────────────────────────────────────────────

    def _list_terminals(self) -> list[_TerminalRecord]:
        """
        Return all visible terminal windows, sorted by Z-order
        (topmost first), with our own windows excluded.
        """
        if not shutil.which("wmctrl"):
            return []

        try:
            out = subprocess.check_output(
                ["wmctrl", "-l", "-p"], text=True, timeout=3.0
            )
        except Exception as exc:
            log.debug("_list_terminals: wmctrl failed — %s", exc)
            return []

        records: list[_TerminalRecord] = []
        for z_order, line in enumerate(out.strip().splitlines()):
            parts = line.split(None, 4)
            if len(parts) < 4:
                continue
            win_id = parts[0]
            if win_id in self._own_window_ids:
                continue
            try:
                pid = int(parts[2])
            except ValueError:
                continue
            title = parts[4] if len(parts) > 4 else ""

            # Filter to terminal windows only
            wm_class = self._get_wm_class(win_id)
            if not self._is_terminal_class(wm_class):
                continue

            pts_path = self._find_pts(pid)
            cwd = self._read_proc_cwd(pid)

            records.append(_TerminalRecord(
                window_id=win_id,
                pid=pid,
                pts_path=pts_path,
                cwd=cwd,
                title=title,
                z_order=z_order,
            ))

        return records

    