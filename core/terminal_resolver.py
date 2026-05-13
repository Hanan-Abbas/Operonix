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
    # Shell environment modifiers — must run in user's shell
    "source", "export", "cd", "alias", "unset", "set",
    "activate", "deactivate", "conda", "nvm", "pyenv", "rbenv",
    # Interactive commands — need a real terminal for password/confirmation prompts.
    # Running these in Ghost silently fails or dumps prompts to the Operonix terminal.
    "sudo",
    # Package managers that prompt for sudo password or y/n confirmation
    "apt", "apt-get", "dpkg", "snap", "flatpak",
    "dnf", "yum", "pacman", "zypper", "brew",
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
        Every 0.5 s, build a ranked terminal list from wmctrl Z-order and
        push the topmost non-blacklisted terminal onto the focus stack.

        WHY NOT xdotool:
          xdotool getactivewindow fails with XGetWindowProperty[_NET_ACTIVE_WINDOW]
          on systems where the panel (Qt/Wayland surface) holds focus. When the
          Operonix panel is open, xdotool returns nothing. This means the focus
          stack was always empty — Bridge was never chosen.

        WHY wmctrl Z-order works:
          wmctrl -l lists windows in Z-order (topmost first). After the user
          focuses a terminal and THEN opens the Operonix panel, the terminal
          is at position 1 or 2 in the list (panel is at 0, terminal is next).
          We push the first non-blacklisted terminal we find.
        """
        while True:
            try:
                await asyncio.sleep(0.5)
                # Run in thread executor so we don't block the event loop
                await asyncio.get_running_loop().run_in_executor(
                    None, self._update_focus_stack_from_zorder
                )
            except asyncio.CancelledError:
                break
            except Exception as exc:
                log.debug("_poll_focus_loop: %s", exc)

    def _update_focus_stack_from_zorder(self) -> None:
        """
        Read wmctrl Z-order and push the topmost visible terminal
        (excluding our own windows) onto the focus stack.

        Called from the polling loop via run_in_executor.
        """
        if not shutil.which("wmctrl"):
            return
        try:
            out = subprocess.check_output(
                ["wmctrl", "-l", "-p"],
                text=True, timeout=2.0,
                stderr=subprocess.DEVNULL,
            )
        except Exception:
            return

        for line in out.strip().splitlines():
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
            if pid in self._own_pids:
                continue

            # Check if this is a terminal window
            wm_class = self._get_wm_class(win_id)
            if self._is_terminal_class(wm_class):
                # Push to focus stack if it's a new top entry
                if not self._focus_stack or self._focus_stack[0] != win_id:
                    self._focus_stack.appendleft(win_id)
                    log.debug(
                        "_update_focus_stack: pushed terminal win_id=%s wm_class=%s",
                        win_id, wm_class.strip(),
                    )
                return  # Only care about the topmost terminal

    @staticmethod
    def _get_active_window_id() -> Optional[str]:
        if shutil.which("xdotool"):
            try:
                # stderr=DEVNULL suppresses "XGetWindowProperty[_NET_ACTIVE_WINDOW]
                # failed" messages that xdotool emits when no X11 window is focused
                # (e.g. when the panel or a Wayland surface is active).
                return subprocess.check_output(
                    ["xdotool", "getactivewindow"],
                    text=True,
                    timeout=1.0,
                    stderr=subprocess.DEVNULL,
                ).strip()
            except Exception:
                pass
        if shutil.which("wmctrl"):
            try:
                out = subprocess.check_output(
                    ["wmctrl", "-l", "-p"], text=True, timeout=1.0,
                    stderr=subprocess.DEVNULL,
                )
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

    @staticmethod
    def _find_pts(pid: int) -> Optional[str]:
        """
        Find the pseudo-terminal device (pts) for a terminal window.

        GNOME Terminal problem:
          wmctrl reports the gnome-terminal-server PID (D-Bus activated).
          The bash shell is several levels deep in its process tree.
          Walking /proc/<pid>/task/<tid>/children only works reliably for
          direct children — gnome-terminal-server may not be the direct
          parent of bash depending on D-Bus activation mode.

        Strategy (three tiers):
          1. Check the reported PID's own fds.
          2. Walk up to 3 levels of children via /proc/<pid>/task/<tid>/children.
          3. Scan ALL /proc/*/fd/ entries for pts devices — filter to processes
             whose session ID (sid) matches the reported PID's session.
             This is the most reliable: bash always has a pts fd, and it
             shares a session with its terminal emulator parent.
        """
        def _pts_from_pid(p: int) -> Optional[str]:
            fd_dir = f"/proc/{p}/fd"
            try:
                for fd in os.listdir(fd_dir):
                    try:
                        target = os.readlink(os.path.join(fd_dir, fd))
                        if target.startswith("/dev/pts/"):
                            return target
                    except OSError:
                        continue
            except OSError:
                pass
            return None

        def _children_of(p: int) -> list[int]:
            children = []
            task_dir = f"/proc/{p}/task"
            try:
                for tid in os.listdir(task_dir):
                    try:
                        with open(f"{task_dir}/{tid}/children") as f:
                            for tok in f.read().split():
                                try:
                                    children.append(int(tok))
                                except ValueError:
                                    pass
                    except OSError:
                        pass
            except OSError:
                pass
            return children

        # Tier 1: direct
        result = _pts_from_pid(pid)
        if result:
            return result

        # Tier 2: walk up to 3 levels of descendants
        frontier = _children_of(pid)
        for _ in range(3):
            next_frontier = []
            for cpid in frontier:
                result = _pts_from_pid(cpid)
                if result:
                    return result
                next_frontier.extend(_children_of(cpid))
            frontier = next_frontier
            if not frontier:
                break

        # Tier 3: scan all /proc/*/fd/ for pts, filtered by session ID
        # Get the session ID of the reported PID (gnome-terminal-server)
        try:
            with open(f"/proc/{pid}/stat") as f:
                stat_fields = f.read().split()
                target_sid = int(stat_fields[5])   # field index 5 = session
        except (OSError, IndexError, ValueError):
            target_sid = None

        if target_sid and target_sid > 1:
            try:
                for entry in os.listdir("/proc"):
                    if not entry.isdigit():
                        continue
                    try:
                        epid = int(entry)
                        # Check session ID matches
                        with open(f"/proc/{epid}/stat") as f:
                            efields = f.read().split()
                            esid = int(efields[5])
                        if esid != target_sid:
                            continue
                        result = _pts_from_pid(epid)
                        if result:
                            log.debug(
                                "_find_pts: found pts=%s via session scan (pid=%d sid=%d)",
                                result, epid, target_sid,
                            )
                            return result
                    except (OSError, IndexError, ValueError):
                        continue
            except OSError:
                pass

        # Tier 4: tty_nr fallback from stat
        try:
            with open(f"/proc/{pid}/stat") as f:
                fields = f.read().split()
                tty_nr = int(fields[6])
                if tty_nr > 0:
                    minor = tty_nr & 0xFF
                    pts = f"/dev/pts/{minor}"
                    if os.path.exists(pts):
                        return pts
        except (OSError, IndexError, ValueError):
            pass
        return None

    @staticmethod
    def _read_proc_cwd(pid: int) -> Optional[str]:
        """
        Read the working directory of the shell inside the terminal.

        Uses the same session-scan fallback as _find_pts: gnome-terminal-server
        cwd is typically / but the bash shell's cwd is what the user sees.
        """
        def _cwd_from_pid(p: int) -> Optional[str]:
            try:
                cwd = os.readlink(f"/proc/{p}/cwd")
                if cwd and cwd not in ("/", "/usr", "/usr/bin", "/usr/local"):
                    return cwd
            except OSError:
                pass
            return None

        # Direct check
        result = _cwd_from_pid(pid)
        if result:
            return result

        # Session scan — find the shell with a real cwd
        try:
            with open(f"/proc/{pid}/stat") as f:
                stat_fields = f.read().split()
                target_sid = int(stat_fields[5])
        except (OSError, IndexError, ValueError):
            target_sid = None

        if target_sid and target_sid > 1:
            try:
                for entry in os.listdir("/proc"):
                    if not entry.isdigit():
                        continue
                    try:
                        epid = int(entry)
                        with open(f"/proc/{epid}/stat") as f:
                            efields = f.read().split()
                            esid = int(efields[5])
                        if esid != target_sid:
                            continue
                        cwd = _cwd_from_pid(epid)
                        if cwd:
                            return cwd
                    except (OSError, IndexError, ValueError):
                        continue
            except OSError:
                pass

        # Return whatever we have even if it's /
        try:
            return os.readlink(f"/proc/{pid}/cwd")
        except OSError:
            return None

    # ── CWD scoring ───────────────────────────────────────────────────────────

    @staticmethod
    def _cwd_score(terminal_cwd: Optional[str], task_cwd: Optional[str]) -> float:
        """
        Return 0.0–1.0 score for how closely terminal_cwd matches task_cwd.

        Rules:
          1.0 — exact match
          0.8 — terminal is a subdirectory of task_cwd
          0.6 — task_cwd is a subdirectory of terminal_cwd (terminal is parent)
          0.3 — same root project (first two path segments match)
          0.0 — no relation
        """
        if not terminal_cwd or not task_cwd:
            return 0.0
        tc = terminal_cwd.rstrip("/")
        sc = task_cwd.rstrip("/")
        if tc == sc:
            return 1.0
        if tc.startswith(sc + "/"):
            return 0.8
        if sc.startswith(tc + "/"):
            return 0.6
        # Compare first two segments
        tc_parts = tc.split("/")[:3]
        sc_parts = sc.split("/")[:3]
        if tc_parts == sc_parts:
            return 0.3
        return 0.0

    # ── Profile hint from command text ───────────────────────────────────────

    @staticmethod
    def _profile_hint_from_command(command: str) -> Optional[str]:
        """
        Inspect the raw command text to see if we can force a profile.
        Returns "ghost", "bridge", "lab", or None (let scoring decide).
        """
        first_token = command.strip().split()[0] if command.strip() else ""
        cmd_lower = command.lower()

        if first_token in _FORCE_BRIDGE:
            return "bridge"
        if any(kw in cmd_lower for kw in _FORCE_LAB):
            return "lab"
        return None

    # ── Main resolution logic ─────────────────────────────────────────────────

    def resolve(
        self,
        cwd: Optional[str] = None,
        command: str = "",
        profile_hint: Optional[str] = None,   # "ghost" | "bridge" | "lab" | None
        venv_path: Optional[str] = None,
    ) -> ResolveResult:
        """
        Determine the optimal execution profile for the given command.

        Args:
            cwd          — working directory from the task payload (from HotkeyListener)
            command      — raw command string (used for force-profile detection)
            profile_hint — explicit override from intent_parser ("ghost"/"bridge"/"lab")
            venv_path    — path to venv activate script, if known

        Returns:
            One of: GhostTarget, BridgeTarget, LabTarget, AmbiguousTarget
        """
        # ── 1. Apply explicit profile hint (intent_parser or capability) ──────
        effective_hint = profile_hint or self._profile_hint_from_command(command)

        if effective_hint == "ghost":
            return GhostTarget(venv_path=venv_path, cwd=cwd)

        if effective_hint == "lab":
            return LabTarget(terminal_bin=self._terminal_bin or "xterm", cwd=cwd)

        # ── 2. Discover available terminals ───────────────────────────────────
        terminals = self._list_terminals()

        if not terminals:
            log.info("TerminalResolver: no terminals found — using Ghost profile")
            return GhostTarget(venv_path=venv_path, cwd=cwd)

        # ── 2.5 Fast path: if focus stack has a recent terminal with a pts,
        # use it immediately without waiting for CWD scoring.
        # This handles the common case where the user:
        #   1. Focused their terminal
        #   2. Opened the panel with Ctrl+Space (panel takes focus)
        #   3. Typed a command
        # In this case cwd reflects the panel/VS Code window, NOT the terminal.
        # CWD scoring would give a poor match, but recency is definitive.
        if self._focus_stack:
            most_recent_id = self._focus_stack[0]
            for rec in terminals:
                if rec.window_id == most_recent_id and rec.pts_path:
                    log.info(
                        "TerminalResolver: fast-path Bridge via focus stack → '%s' (pts=%s)",
                        rec.title, rec.pts_path,
                    )
                    return BridgeTarget(
                        pts_path=rec.pts_path,
                        window_id=rec.window_id,
                        window_title=rec.title,
                        cwd=rec.cwd,
                    )

        # ── 3. Score each terminal by CWD match + temporal recency ────────────
        scored: list[tuple[float, _TerminalRecord]] = []
        for rec in terminals:
            cwd_s = self._cwd_score(rec.cwd, cwd)
            # Recency bonus: position in focus_stack (0 = most recent)
            try:
                stack_pos = list(self._focus_stack).index(rec.window_id)
                recency = max(0.0, 1.0 - stack_pos * 0.15)
            except ValueError:
                recency = 0.0
            score = cwd_s * 0.7 + recency * 0.3
            scored.append((score, rec))
            log.debug(
                "TerminalResolver: scored terminal='%s' cwd='%s' cwd_score=%.2f recency=%.2f total=%.2f pts=%s",
                rec.title, rec.cwd, cwd_s, recency, score, rec.pts_path,
            )

        scored.sort(key=lambda x: x[0], reverse=True)

        best_score, best_rec = scored[0]

        # ── 4. Check for ambiguity ─────────────────────────────────────────────
        top_group = [
            rec for score, rec in scored
            if abs(score - best_score) < 0.05 and best_score > 0.0
        ]

        if len(top_group) > 1 and effective_hint == "bridge":
            candidates = [
                BridgeTarget(
                    pts_path=rec.pts_path or "",
                    window_id=rec.window_id,
                    window_title=rec.title,
                    cwd=rec.cwd,
                )
                for rec in top_group
                if rec.pts_path
            ]
            if len(candidates) > 1:
                log.info("TerminalResolver: ambiguous Bridge — %d candidates", len(candidates))
                return AmbiguousTarget(candidates=candidates)

        # ── 5. Decide profile based on best score ─────────────────────────────
        #
        # FIX: lowered threshold from 0.3 to 0.0 and made recency alone
        # sufficient to choose Bridge.  Previously a terminal with no CWD match
        # but recent focus (recency=1.0) scored 0.3 exactly, which only passed
        # if >= 0.3 AND pts_path was found.  Now ANY terminal with a pts_path
        # that was recently focused is preferred over Ghost.
        #
        # The old 0.3 threshold was designed to avoid picking a random terminal
        # with no relation to the task. With recency tracking this is no longer
        # needed — the focus stack guarantees the user explicitly focused that
        # terminal before opening the panel.
        if best_rec.pts_path:
            log.info(
                "TerminalResolver: Bridge → '%s' (score=%.2f, pts=%s, cwd=%s)",
                best_rec.title, best_score, best_rec.pts_path, best_rec.cwd,
            )
            return BridgeTarget(
                pts_path=best_rec.pts_path,
                window_id=best_rec.window_id,
                window_title=best_rec.title,
                cwd=best_rec.cwd,
            )

        if effective_hint == "bridge":
            log.info("TerminalResolver: bridge requested but no pts found — falling to Lab")
            return LabTarget(terminal_bin=self._terminal_bin or "xterm", cwd=cwd)

        # No terminals have a pts — fall back to Ghost
        log.info(
            "TerminalResolver: Ghost profile — no pts found on any terminal (best_score=%.2f)",
            best_score,
        )
        return GhostTarget(venv_path=venv_path, cwd=cwd)


# Singleton
terminal_resolver = TerminalResolver()