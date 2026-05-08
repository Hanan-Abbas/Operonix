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
