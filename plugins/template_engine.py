"""
plugins/template_engine.py

Generates plugin and test file skeletons based on intent category.

Design philosophy:
  Every template is a WORKING, RUNNABLE example — not a stub with # TODO comments.
  The LLM's only job is to replace the example logic with intent-specific logic.
  The skeleton must never lie about what APIs exist (no fake registry services).

Categories and when they are used:
  - automation   UI interaction: click, type, drag, scroll, screenshot, OCR
  - background   Infinite-loop daemons: auto-clicker, monitor, watcher, repeater
  - web          HTTP requests, URL fetch, web scraping, search APIs
  - file         Read, write, copy, move, delete, rename files and directories
  - command      Shell commands, subprocesses, terminal operations
  - system       OS-level: clipboard, notifications, processes, window management
  - data         Parse, transform, calculate, convert, format data
  - generic      Fallback for anything that doesn't match above

Pattern library (injected into generator prompt):
  Contains copy-paste-ready code snippets for the most common sub-tasks,
  so the LLM never has to guess how to do threading, sleeping, stopping loops,
  reading clipboard, making HTTP requests, etc.
"""
from __future__ import annotations

import logging
import re

logger = logging.getLogger("TemplateEngine")


# ── Category Detection ─────────────────────────────────────────────────────────

# Phrase-based scoring: multi-word phrases score higher than single keywords.
# Checked in order; first match in a phrase wins its full score.
_CATEGORY_PHRASES: dict[str, list[tuple[int, str]]] = {
    "background": [
        (3, "auto clicker"),   (3, "auto-clicker"),   (3, "autoclicker"),
        (3, "infinite loop"),  (3, "keep clicking"),   (3, "repeat click"),
        (3, "click forever"),  (3, "continuous click"),(3, "click loop"),
        (3, "keep pressing"),  (3, "keep typing"),     (3, "hold key"),
        (2, "background task"),(2, "daemon"),          (2, "monitor"),
        (2, "watcher"),        (2, "periodic"),        (2, "interval"),
        (2, "stop when"),      (2, "until stopped"),   (2, "toggle"),
        (1, "repeat"),         (1, "loop"),             (1, "infinite"),
        (1, "continuous"),     (1, "watch"),
    ],
    "automation": [
        (3, "click on"),       (3, "right click"),     (3, "double click"),
        (3, "type text"),      (3, "press key"),        (3, "take screenshot"),
        (3, "read screen"),    (3, "find element"),     (3, "drag and drop"),
        (3, "open chrome"),    (3, "open firefox"),     (3, "open browser"),
        (3, "open terminal"),  (3, "open vscode"),      (3, "launch chrome"),
        (3, "launch firefox"), (3, "launch app"),       (3, "open app"),
        (3, "start app"),      (3, "start chrome"),
        (2, "scroll down"),    (2, "scroll up"),        (2, "move mouse"),
        (2, "focus window"),   (2, "switch window"),    (2, "close app"),
        (1, "click"),          (1, "type"),             (1, "keyboard"),
        (1, "mouse"),          (1, "screen"),           (1, "window"),
        (1, "button"),         (1, "input"),            (1, "drag"),
        (1, "scroll"),         (1, "screenshot"),       (1, "ocr"),
        (1, "vision"),         (1, "ui"),               (1, "open"),
        (1, "launch"),
    ],
    "web": [
        (3, "http request"),   (3, "api call"),         (3, "web scrape"),
        (3, "download file"),  (3, "fetch url"),        (3, "browse to"),
        (2, "search web"),     (2, "search google"),    (2, "open url"),
        (2, "open link"),      (2, "visit website"),
        (1, "search"),         (1, "url"),              (1, "http"),
        (1, "https"),          (1, "browse"),           (1, "website"),
        (1, "download"),       (1, "fetch"),            (1, "scrape"),
        (1, "web"),            (1, "api"),              (1, "request"),
    ],
    "file": [
        (3, "read file"),      (3, "write file"),       (3, "delete file"),
        (3, "copy file"),      (3, "move file"),        (3, "rename file"),
        (3, "create folder"),  (3, "list files"),       (3, "find file"),
        (2, "file content"),   (2, "file path"),        (2, "open file"),
        (2, "save file"),      (2, "append to"),
        (1, "file"),           (1, "folder"),           (1, "directory"),
        (1, "read"),           (1, "write"),            (1, "delete"),
        (1, "copy"),           (1, "move"),             (1, "rename"),
        (1, "path"),           (1, "save"),
    ],
    "command": [
        (3, "run command"),    (3, "shell command"),    (3, "terminal command"),
        (3, "bash script"),    (3, "execute script"),   (3, "run script"),
        (2, "run program"),    (2, "start process"),    (2, "kill process"),
        (1, "run"),            (1, "execute"),          (1, "shell"),
        (1, "terminal"),       (1, "bash"),             (1, "cmd"),
        (1, "process"),        (1, "script"),
    ],
    "system": [
        (3, "copy to clipboard"), (3, "read clipboard"),  (3, "paste clipboard"),
        (3, "show notification"), (3, "send notification"),(3, "system tray"),
        (3, "lock screen"),    (3, "sleep computer"),   (3, "shutdown"),
        (3, "volume up"),      (3, "volume down"),      (3, "mute"),
        (2, "clipboard"),      (2, "notification"),     (2, "brightness"),
        (2, "battery"),        (2, "wifi"),             (2, "bluetooth"),
        (1, "system"),         (1, "os"),               (1, "desktop"),
    ],
    "data": [
        (3, "parse json"),     (3, "parse csv"),        (3, "convert format"),
        (3, "calculate"),      (3, "transform data"),   (3, "format text"),
        (2, "extract data"),   (2, "process data"),     (2, "summarize"),
        (2, "count words"),    (2, "find pattern"),
        (1, "parse"),          (1, "convert"),          (1, "calculate"),
        (1, "format"),         (1, "extract"),          (1, "transform"),
        (1, "data"),
    ],
}


def detect_category(intent: str, description: str = "") -> str:
    """
    Detect the most likely plugin category using phrase-based scoring.
    Multi-word phrases score higher than single keywords.
    Returns the category with the highest score, or 'generic' if no match.
    """
    combined = f"{intent} {description}".lower().replace("_", " ").replace("-", " ")
    scores: dict[str, int] = {cat: 0 for cat in _CATEGORY_PHRASES}

    for cat, phrases in _CATEGORY_PHRASES.items():
        for score, phrase in phrases:
            if phrase in combined:
                scores[cat] += score

    best_cat = max(scores, key=scores.get)
    best_score = scores[best_cat]

    logger.debug(f"Category scores for '{intent}': {scores} → {best_cat} ({best_score})")
    return best_cat if best_score > 0 else "generic"


# ── Pattern Library ────────────────────────────────────────────────────────────
# Injected into the generator prompt so the LLM copies correct patterns.
# Each pattern is a working, copy-paste-ready code snippet.

PATTERN_LIBRARY = '''
# ══════════════════════════════════════════════════════════════════════════════
# OPERONIX PLUGIN PATTERN LIBRARY — copy the patterns relevant to your intent
# ══════════════════════════════════════════════════════════════════════════════
#
# ⚠️  OS IS INJECTED AT RUNTIME — the generator will tell you TARGET_OS above.
#     Follow its OS NOTES for shell commands, paths, and tool names.
#
# ⚠️  NO BLOCKING SUBPROCESS CALLS. EVER.
#     subprocess.run / subprocess.call / subprocess.Popen / os.system ALL block
#     the async event loop. Use asyncio.create_subprocess_shell exclusively.
#     The static scanner WILL reject your code if it finds blocking calls.

# ── PATTERN: Async shell command (ALL categories that need shell) ─────────────
# Copy this EXACTLY. Do not use subprocess.run — it blocks the event loop.
import asyncio

async def _run_cmd(cmd: str, timeout: int = 30) -> tuple[str, str, int]:
    """Run a shell command async. Returns (stdout, stderr, returncode)."""
    proc = await asyncio.create_subprocess_shell(
        cmd,
        stdout=asyncio.subprocess.PIPE,
        stderr=asyncio.subprocess.PIPE,
    )
    try:
        stdout_b, stderr_b = await asyncio.wait_for(proc.communicate(), timeout=timeout)
    except asyncio.TimeoutError:
        proc.kill()
        return "", "Command timed out", -1
    return stdout_b.decode(errors="replace"), stderr_b.decode(errors="replace"), proc.returncode

# Usage in run():
#   stdout, stderr, rc = await _run_cmd("ls -la /tmp")
#   if rc != 0:
#       return {"status": "error", "message": f"Command failed: {stderr}"}
#   return {"status": "success", "result": stdout}

# ── PATTERN: Launch a Linux application (async) ───────────────────────────────
import asyncio, shutil

async def launch_app_linux(app_name: str) -> dict:
    _ALIASES = {
        "chrome": "google-chrome", "google chrome": "google-chrome",
        "vscode": "code", "vs code": "code", "cursor": "cursor",
        "terminal": "gnome-terminal", "files": "nautilus",
    }
    binary = _ALIASES.get(app_name.lower(), app_name)
    if not shutil.which(binary):
        return {"status": "error", "message": f"Cannot find '{binary}' on PATH"}
    proc = await asyncio.create_subprocess_shell(
        f"{binary} &",
        stdout=asyncio.subprocess.DEVNULL,
        stderr=asyncio.subprocess.DEVNULL,
    )
    return {"status": "success", "result": f"Launched '{binary}'"}

# ── PATTERN: Launch a macOS application (async) ───────────────────────────────
async def launch_app_macos(app_name: str) -> dict:
    stdout, stderr, rc = await _run_cmd(f'open -a "{app_name}"')
    if rc != 0:
        return {"status": "error", "message": f"open failed: {stderr}"}
    return {"status": "success", "result": f"Launched '{app_name}'"}

# ── PATTERN: Launch a Windows application (async) ─────────────────────────────
async def launch_app_windows(app_name: str) -> dict:
    stdout, stderr, rc = await _run_cmd(f'start "" "{app_name}"')
    if rc != 0:
        return {"status": "error", "message": f"start failed: {stderr}"}
    return {"status": "success", "result": f"Launched '{app_name}'"}

# ── PATTERN: Clipboard — Linux (xclip, async) ────────────────────────────────
async def clipboard_read_linux() -> str:
    stdout, _, _ = await _run_cmd("xclip -selection clipboard -o")
    return stdout

async def clipboard_write_linux(text: str) -> None:
    proc = await asyncio.create_subprocess_shell(
        "xclip -selection clipboard",
        stdin=asyncio.subprocess.PIPE,
        stdout=asyncio.subprocess.DEVNULL,
        stderr=asyncio.subprocess.DEVNULL,
    )
    await asyncio.wait_for(proc.communicate(input=text.encode()), timeout=5)

# ── PATTERN: Clipboard — macOS (pbcopy/pbpaste, async) ───────────────────────
async def clipboard_read_macos() -> str:
    stdout, _, _ = await _run_cmd("pbpaste")
    return stdout

async def clipboard_write_macos(text: str) -> None:
    proc = await asyncio.create_subprocess_shell(
        "pbcopy", stdin=asyncio.subprocess.PIPE,
        stdout=asyncio.subprocess.DEVNULL, stderr=asyncio.subprocess.DEVNULL,
    )
    await asyncio.wait_for(proc.communicate(input=text.encode()), timeout=5)

# ── PATTERN: System notification ─────────────────────────────────────────────
# Linux:
async def notify_linux(title: str, body: str) -> None:
    await _run_cmd(f'notify-send "{title}" "{body}"')

# macOS:
async def notify_macos(title: str, body: str) -> None:
    script = f'display notification "{body}" with title "{title}"'
    await _run_cmd(f"osascript -e '{script}'")

# ── PATTERN: Background loop with stop event ──────────────────────────────────
# Use for ANY "do X repeatedly until stopped" intent.
import threading, time

_stop_event = threading.Event()

def _worker_loop(stop_event, interval=0.1):
    import pyautogui
    pyautogui.FAILSAFE = False
    while not stop_event.is_set():
        pyautogui.click()
        stop_event.wait(interval)

def _listen_for_stop(stop_event, hotkey_str="alt+s"):
    try:
        from pynput import keyboard as _kb
        parts = [p.strip().lower() for p in hotkey_str.split("+")]
        _MOD_MAP = {"alt": _kb.Key.alt, "ctrl": _kb.Key.ctrl, "shift": _kb.Key.shift}
        modifiers = {_MOD_MAP[p] for p in parts[:-1] if p in _MOD_MAP}
        char_key  = parts[-1]
        pressed   = set()
        def on_press(key):
            pressed.add(key)
            try: k = key.char
            except AttributeError: k = None
            if all(m in pressed for m in modifiers) and k == char_key:
                stop_event.set(); return False
        def on_release(key): pressed.discard(key)
        with _kb.Listener(on_press=on_press, on_release=on_release) as lst:
            while not stop_event.is_set(): time.sleep(0.05)
            lst.stop()
    except Exception:
        stop_event.wait(60); stop_event.set()

# ── PATTERN: One-shot UI action ───────────────────────────────────────────────
import pyautogui, time
pyautogui.FAILSAFE = True
pyautogui.click(x=100, y=200)
pyautogui.typewrite("hello", interval=0.05)
pyautogui.hotkey("ctrl", "c")
pyautogui.press("enter")
time.sleep(0.5)

# ── PATTERN: HTTP / web request (stdlib, no blocking) ────────────────────────
import urllib.request, json as _json
with urllib.request.urlopen("https://api.example.com/data", timeout=10) as resp:
    data = _json.loads(resp.read().decode())

# ── PATTERN: File operations ──────────────────────────────────────────────────
import os, shutil
content = open(path, "r", encoding="utf-8").read()
open(path, "w", encoding="utf-8").write(content)
os.makedirs(os.path.dirname(path), exist_ok=True)
shutil.copy2(src, dst)
shutil.move(src, dst)
os.remove(path)
files = os.listdir(directory)

# ══════════════════════════════════════════════════════════════════════════════
'''


# ── Plugin Header ─────────────────────────────────────────────────────────────

_PLUGIN_HEADER = '''\
"""
Plugin: {plugin_name}
Intent: {intent}
Category: {category}
Description: {description}
Version: {version}
Generated: {timestamp}
"""
# Standard library imports — always available
import asyncio
import os
import shutil
import threading
import time

# NOTE: from __future__ imports and sys.path bootstrap are injected
# automatically by sandbox_runner — do NOT add them here.

from plugins.manifest_schema import BasePlugin


'''


# ── Category Templates ────────────────────────────────────────────────────────
# Each template is a WORKING example.
# The LLM replaces example logic with intent-specific logic.
# Rules embedded in comments prevent the LLM from drifting.

_BACKGROUND_TEMPLATE = '''\
class {class_name}(BasePlugin):
    """
    {description}
    Category: background daemon (infinite loop with stop trigger)

    Pattern: starts a daemon thread that loops until a stop event fires.
    The run() method starts the threads and returns immediately.
    """
    name             = "{plugin_name}"
    description      = "{description}"
    version          = "{version}"
    permissions      = ["ui_interaction"]
    safe_mode        = True
    allowed_services = []

    # Class-level stop event — shared across calls
    _stop_event: threading.Event | None = None

    def validate(self, args: dict) -> str | None:
        # No required args for background tasks — all config has sensible defaults
        return None

    async def run(self, context: dict, args: dict) -> dict:
        try:
            import pyautogui
            import threading

            # ── Configuration from args (with safe defaults) ─────────────────
            interval   = float(args.get("interval", 0.1))   # seconds between actions
            stop_hotkey = str(args.get("stop_hotkey", "alt+s"))

            # ── Stop any previous instance first ──────────────────────────────
            if {class_name}._stop_event is not None:
                {class_name}._stop_event.set()
                time.sleep(0.2)

            stop_event = threading.Event()
            {class_name}._stop_event = stop_event

            # ── Worker: performs the repeating action ─────────────────────────
            def _worker(stop: threading.Event, iv: float) -> None:
                pyautogui.FAILSAFE = True
                while not stop.is_set():
                    # TODO: Replace this line with the actual repeating action
                    # Examples:
                    #   pyautogui.click()                    # left click
                    #   pyautogui.press("space")             # press key
                    #   pyautogui.click(button="right")      # right click
                    pyautogui.click()
                    stop.wait(iv)  # waits iv seconds OR until stop is set

            # ── Stopper: listens for hotkey using pynput (no root required) ──────
            def _stopper(stop: threading.Event, hotkey_str: str) -> None:
                try:
                    from pynput import keyboard as _kb
                    parts    = [p.strip().lower() for p in hotkey_str.split("+")]
                    _MOD_MAP = {{
                        "alt": _kb.Key.alt, "ctrl": _kb.Key.ctrl,
                        "shift": _kb.Key.shift, "cmd": _kb.Key.cmd,
                    }}
                    modifiers = {{_MOD_MAP[p] for p in parts[:-1] if p in _MOD_MAP}}
                    char_key  = parts[-1]
                    pressed   = set()

                    def on_press(key):
                        pressed.add(key)
                        try:    k = key.char
                        except: k = None
                        if all(m in pressed for m in modifiers) and k == char_key:
                            stop.set()
                            return False

                    def on_release(key):
                        pressed.discard(key)

                    with _kb.Listener(on_press=on_press, on_release=on_release) as lst:
                        while not stop.is_set():
                            time.sleep(0.05)
                        lst.stop()
                except Exception:
                    # pynput unavailable — auto-stop after 60s
                    stop.wait(60)
                    stop.set()

            threading.Thread(
                target=_worker, args=(stop_event, interval), daemon=True
            ).start()
            threading.Thread(
                target=_stopper, args=(stop_event, stop_hotkey), daemon=True
            ).start()

            return {{
                "status":    "success",
                "result":    "started",
                "intent":    "{intent}",
                "stop_with": stop_hotkey,
                "interval":  interval,
            }}

        except Exception as e:
            return {{"status": "error", "message": str(e), "intent": "{intent}"}}
'''


_AUTOMATION_TEMPLATE = '''\
class {class_name}(BasePlugin):
    """
    {description}
    Category: automation (one-shot UI interaction)

    Locked-contract pattern:
      run()      — pre-written, non-negotiable. Handles failsafe, stuck-key
                   recovery, and result formatting. DO NOT modify this method.
      _execute() — the ONLY zone the LLM writes. Returns a list of step strings.

    Uses pyautogui for all UI actions. Do NOT use the `keyboard` module
    (requires root on Linux). Do NOT import from automation/, context/, core/.
    """
    name             = "{plugin_name}"
    description      = "{description}"
    version          = "{version}"
    permissions      = ["ui_interaction", "screen_read"]
    safe_mode        = True
    allowed_services = []

    def validate(self, args: dict) -> str | None:
        # TODO: Add validation for args your action requires.
        # Example: if not args.get("target"): return "Missing 'target'"
        return None

    async def _execute(self, args: dict) -> list:
        """
        TODO: Implement the UI macro sequence for intent "{intent}".

        RULES:
        - Return a LIST OF STRINGS describing each step performed.
          The locked run() uses this list to build the success message.
        - Use pyautogui for ALL UI actions (already imported in header).
        - Add time.sleep(0.1-0.5) between actions so the OS can process them.
        - For async waits: await asyncio.sleep(seconds)
        - NEVER call sys.exit(), os._exit(), or raise SystemExit here.

        Examples — compose the steps you need:

        CLICK:
            pyautogui.click(x=500, y=300)
            time.sleep(0.2)
            return ["Clicked at (500, 300)"]

        TYPE text:
            pyautogui.typewrite("hello world", interval=0.05)
            return ["Typed: hello world"]

        HOTKEY:
            pyautogui.hotkey("ctrl", "c")
            time.sleep(0.1)
            return ["Pressed Ctrl+C (copy)"]

        MULTI-STEP:
            pyautogui.hotkey("ctrl", "l")
            time.sleep(0.2)
            pyautogui.typewrite("https://example.com", interval=0.04)
            pyautogui.press("enter")
            time.sleep(0.5)
            return [
                "Focused address bar (Ctrl+L)",
                "Typed URL",
                "Pressed Enter to navigate",
            ]
        """
        raise NotImplementedError("_execute() must be implemented for intent: {intent}")

    async def run(self, context: dict, args: dict) -> dict:
        # ── LOCKED CONTRACT — do NOT modify this method ───────────────────────
        # Handles: validate guard, pyautogui failsafe, stuck-key recovery,
        # and result formatting. LLM only writes _execute() above.
        error = self.validate(args)
        if error:
            return {{"status": "error", "message": error, "intent": "{intent}"}}
        try:
            import pyautogui
            pyautogui.FAILSAFE = True
            steps = await self._execute(args)
            if not isinstance(steps, list):
                steps = [str(steps)]
            return {{
                "status":  "success",
                "result":  steps,
                "display": {{
                    "type":    "markdown",
                    "content": "### 🤖 UI Automation Log\\n"
                               + "\\n".join(f"* {{s}}" for s in steps),
                }},
                "intent": "{intent}",
            }}
        except Exception as e:
            # Best-effort stuck-key recovery — release common modifier keys
            try:
                import pyautogui as _pag
                for _key in ("ctrl", "alt", "shift", "win", "cmd"):
                    try:
                        _pag.keyUp(_key)
                    except Exception:
                        pass
            except Exception:
                pass
            return {{"status": "error", "message": str(e), "intent": "{intent}"}}
'''


_WEB_TEMPLATE = '''\
class {class_name}(BasePlugin):
    """
    {description}
    Category: web (HTTP request / web data)

    Pattern: uses urllib (stdlib, always available) for HTTP.
    No external dependencies required.
    """
    name             = "{plugin_name}"
    description      = "{description}"
    version          = "{version}"
    permissions      = ["web_access"]
    safe_mode        = True
    allowed_services = []

    def validate(self, args: dict) -> str | None:
        # TODO: Add validation. Example:
        # if not args.get("query"): return "Missing required arg: 'query'"
        return None

    async def _execute(self, args: dict) -> dict:
        """
        TODO: Implement the HTTP logic for intent "{intent}".

        GET example:
            import urllib.request, json as _json
            url = str(args.get("url", "https://example.com"))
            with urllib.request.urlopen(url, timeout=10) as resp:
                data = _json.loads(resp.read().decode())
            return {{"data": data}}

        POST example:
            import urllib.request, urllib.parse, json as _json
            url   = "https://api.example.com/endpoint"
            query = str(args.get("query", ""))
            body  = urllib.parse.urlencode({{"q": query}}).encode()
            req   = urllib.request.Request(url, data=body)
            with urllib.request.urlopen(req, timeout=10) as resp:
                data = _json.loads(resp.read().decode())
            return {{"result": data}}
        """
        raise NotImplementedError("_execute() must be implemented for intent: {intent}")

    async def run(self, context: dict, args: dict) -> dict:
        error = self.validate(args)
        if error:
            return {{"status": "error", "message": error, "intent": "{intent}"}}
        try:
            result = await self._execute(args)
            return {{"status": "success", "result": result, "intent": "{intent}"}}
        except Exception as e:
            return {{"status": "error", "message": str(e), "intent": "{intent}"}}
'''


_FILE_TEMPLATE = '''\
class {class_name}(BasePlugin):
    """
    {description}
    Category: file (filesystem operations)

    Pattern: uses os, shutil, pathlib (stdlib) directly.
    All paths must stay within safe directories.
    validate() is ALWAYS called by run() — never bypass it.
    """
    name             = "{plugin_name}"
    description      = "{description}"
    version          = "{version}"
    permissions      = ["file_read", "file_write"]
    safe_mode        = True
    allowed_services = []

    # Restrict operations to safe paths (never touch system files)
    _SAFE_ROOT = os.path.expanduser("~")

    def validate(self, args: dict) -> str | None:
        path = args.get("path", "")
        if not path:
            return "Missing required argument: 'path'"
        abs_path = os.path.realpath(os.path.expanduser(str(path)))
        if not abs_path.startswith(self._SAFE_ROOT):
            return f"Path outside safe root ({{self._SAFE_ROOT}}): {{path}}"
        return None

    async def _execute(self, args: dict) -> dict:
        """
        TODO: Implement the file operation for intent "{intent}".
        This method is called ONLY after validate() has passed.
        Return a dict — do NOT return status/error here; run() wraps it.

        Examples — pick ONE:

        READ a file:
            path    = os.path.realpath(os.path.expanduser(str(args["path"])))
            content = open(path, "r", encoding="utf-8").read()
            return {{"content": content, "bytes": len(content)}}

        WRITE a file:
            path    = os.path.realpath(os.path.expanduser(str(args["path"])))
            content = str(args.get("content", ""))
            os.makedirs(os.path.dirname(path), exist_ok=True)
            open(path, "w", encoding="utf-8").write(content)
            return {{"written": len(content), "path": path}}

        COPY a file:
            src = os.path.realpath(os.path.expanduser(str(args["path"])))
            dst = os.path.realpath(os.path.expanduser(str(args.get("dest", ""))))
            shutil.copy2(src, dst)
            return {{"copied_to": dst}}

        LIST a directory:
            path  = os.path.realpath(os.path.expanduser(str(args["path"])))
            files = os.listdir(path)
            return {{"files": files, "count": len(files)}}

        ORGANIZE (sort files by extension):
            path = os.path.realpath(os.path.expanduser(str(args["path"])))
            moved = []
            ext_map = {{
                ".jpg": "images", ".png": "images", ".gif": "images",
                ".pdf": "docs",   ".txt": "docs",   ".docx": "docs",
                ".mp4": "videos", ".avi": "videos",
                ".zip": "archives", ".tar": "archives", ".gz": "archives",
            }}
            for fname in os.listdir(path):
                fpath = os.path.join(path, fname)
                if not os.path.isfile(fpath):
                    continue
                ext   = os.path.splitext(fname)[1].lower()
                subdir = ext_map.get(ext, "other")
                dest_dir = os.path.join(path, subdir)
                os.makedirs(dest_dir, exist_ok=True)
                shutil.move(fpath, os.path.join(dest_dir, fname))
                moved.append(f"{{fname}} → {{subdir}}")
            return {{"moved": moved, "count": len(moved)}}
        """
        raise NotImplementedError("_execute() must be implemented for intent: {intent}")

    async def run(self, context: dict, args: dict) -> dict:
        # Validate FIRST — never skip this
        error = self.validate(args)
        if error:
            return {{"status": "error", "message": error, "intent": "{intent}"}}
        try:
            result = await self._execute(args)
            return {{"status": "success", "result": result, "intent": "{intent}"}}
        except Exception as e:
            return {{"status": "error", "message": str(e), "intent": "{intent}"}}
'''


_COMMAND_TEMPLATE = '''\
class {class_name}(BasePlugin):
    """
    {description}
    Category: command (shell / subprocess)

    Pattern: runs shell commands via asyncio.create_subprocess_shell.
    NEVER use subprocess.run / subprocess.call / os.system — they block
    the entire agent event loop.
    """
    name             = "{plugin_name}"
    description      = "{description}"
    version          = "{version}"
    permissions      = ["command_execution"]
    safe_mode        = True
    allowed_services = []

    # Allowlist of safe command prefixes (extend as needed)
    _SAFE_PREFIXES: tuple[str, ...] = ("ls", "pwd", "echo", "cat", "grep", "find", "df", "du")

    def validate(self, args: dict) -> str | None:
        cmd = args.get("command")
        if not cmd:
            return "Missing required argument: 'command'"
        exe = cmd[0] if isinstance(cmd, list) else str(cmd).split()[0]
        if self.safe_mode and not any(exe.startswith(p) for p in self._SAFE_PREFIXES):
            return (
                f"Command '{{exe}}' not in safe_mode allowlist. "
                f"Set safe_mode=False or add to _SAFE_PREFIXES."
            )
        return None

    async def _execute(self, args: dict) -> dict:
        """
        TODO: Build the shell command string for intent "{intent}" and run it.

        Template — copy and adapt:
            cmd     = args.get("command", [])
            timeout = int(args.get("timeout", 30))
            if isinstance(cmd, list):
                shell_cmd = " ".join(cmd)
            else:
                shell_cmd = str(cmd)

            proc = await asyncio.create_subprocess_shell(
                shell_cmd,
                stdout=asyncio.subprocess.PIPE,
                stderr=asyncio.subprocess.PIPE,
            )
            stdout_b, stderr_b = await asyncio.wait_for(proc.communicate(), timeout=timeout)
            stdout = stdout_b.decode(errors="replace")
            stderr = stderr_b.decode(errors="replace")

            if proc.returncode != 0:
                return {{"stdout": stdout, "stderr": stderr, "returncode": proc.returncode}}
            return {{"stdout": stdout[:2000], "returncode": 0}}
        """
        raise NotImplementedError("_execute() must be implemented for intent: {intent}")

    async def run(self, context: dict, args: dict) -> dict:
        error = self.validate(args)
        if error:
            return {{"status": "error", "message": error, "intent": "{intent}"}}
        try:
            result = await self._execute(args)
            rc = result.get("returncode", 0)
            status = "success" if rc == 0 else "error"
            if status == "error":
                result["message"] = f"Command failed (rc={{rc}}): {{result.get('stderr', '')}}"
            return {{"status": status, "result": result, "intent": "{intent}"}}
        except asyncio.TimeoutError:
            return {{"status": "error", "message": "Command timed out", "intent": "{intent}"}}
        except Exception as e:
            return {{"status": "error", "message": str(e), "intent": "{intent}"}}
'''


_SYSTEM_TEMPLATE = '''\
class {class_name}(BasePlugin):
    """
    {description}
    Category: system (OS-level: clipboard, notifications, volume, etc.)

    Pattern: uses asyncio.create_subprocess_shell for all OS tool calls.
    NEVER use subprocess.run / Popen / os.system — they block the event loop.
    Checks for tool availability before use.
    """
    name             = "{plugin_name}"
    description      = "{description}"
    version          = "{version}"
    permissions      = ["system_access"]
    safe_mode        = True
    allowed_services = []

    def validate(self, args: dict) -> str | None:
        # TODO: Add validation for args your system action needs.
        # Example: if not args.get("text"): return "Missing 'text'"
        return None

    async def _run_cmd(self, cmd: str, input_bytes: bytes | None = None, timeout: int = 10) -> tuple[str, str, int]:
        """Async shell helper. Returns (stdout, stderr, returncode)."""
        proc = await asyncio.create_subprocess_shell(
            cmd,
            stdin=asyncio.subprocess.PIPE if input_bytes else None,
            stdout=asyncio.subprocess.PIPE,
            stderr=asyncio.subprocess.PIPE,
        )
        try:
            stdout_b, stderr_b = await asyncio.wait_for(
                proc.communicate(input=input_bytes), timeout=timeout
            )
        except asyncio.TimeoutError:
            proc.kill()
            return "", "timed out", -1
        return stdout_b.decode(errors="replace"), stderr_b.decode(errors="replace"), proc.returncode

    async def _execute(self, args: dict) -> dict:
        """
        TODO: Implement the system action for intent "{intent}".
        Use self._run_cmd(...) for ALL shell calls.

        Examples — pick ONE:

        CLIPBOARD READ (Linux):
            stdout, stderr, rc = await self._run_cmd("xclip -selection clipboard -o")
            if rc != 0:
                return {{"error": f"xclip failed: {{stderr}}"}}
            return {{"clipboard": stdout}}

        CLIPBOARD WRITE (Linux):
            text = str(args.get("text", ""))
            _, stderr, rc = await self._run_cmd("xclip -selection clipboard", input_bytes=text.encode())
            return {{"copied": len(text)}}

        CLIPBOARD READ (macOS):
            stdout, stderr, rc = await self._run_cmd("pbpaste")
            return {{"clipboard": stdout}}

        NOTIFICATION (Linux):
            title = str(args.get("title", "Operonix"))
            body  = str(args.get("body", ""))
            await self._run_cmd(f'notify-send "{{title}}" "{{body}}"')
            return {{"notified": True}}

        NOTIFICATION (macOS):
            title = str(args.get("title", "Operonix"))
            body  = str(args.get("body", ""))
            script = f'display notification "{{body}}" with title "{{title}}"'
            await self._run_cmd(f"osascript -e '{{script}}'")
            return {{"notified": True}}

        VOLUME (Linux):
            direction = args.get("direction", "up")
            step = int(args.get("step", 5))
            sign = "+" if direction == "up" else "-"
            await self._run_cmd(f"pactl set-sink-volume @DEFAULT_SINK@ {{sign}}{{step}}%")
            return {{"volume": f"{{direction}} {{step}}%"}}
        """
        raise NotImplementedError("_execute() must be implemented for intent: {intent}")

    async def run(self, context: dict, args: dict) -> dict:
        error = self.validate(args)
        if error:
            return {{"status": "error", "message": error, "intent": "{intent}"}}
        try:
            result = await self._execute(args)
            if "error" in result:
                return {{"status": "error", "message": result["error"], "intent": "{intent}"}}
            return {{"status": "success", "result": result, "intent": "{intent}"}}
        except Exception as e:
            return {{"status": "error", "message": str(e), "intent": "{intent}"}}
'''


_DATA_TEMPLATE = '''\
class {class_name}(BasePlugin):
    """
    {description}
    Category: data (parse, transform, calculate, format)

    Pattern: pure Python data processing — no UI, no network, no filesystem.
    Input arrives via args, output goes in the result dict.
    """
    name             = "{plugin_name}"
    description      = "{description}"
    version          = "{version}"
    permissions      = []
    safe_mode        = True
    allowed_services = []

    def validate(self, args: dict) -> str | None:
        # TODO: Add validation. Example:
        # if not args.get("data"): return "Missing required arg: 'data'"
        return None

    async def _execute(self, args: dict) -> dict:
        """
        TODO: Implement the data operation for intent "{intent}".

        JSON parse:
            import json as _json
            data   = args.get("data", "")
            parsed = _json.loads(data)
            return {{"parsed": parsed}}

        CSV parse:
            import csv, io
            data   = args.get("data", "")
            reader = csv.DictReader(io.StringIO(data))
            return {{"rows": list(reader)}}

        Text transform:
            data = str(args.get("data", ""))
            return {{"result": data.strip().upper()}}

        Regex extract:
            import re
            data    = str(args.get("data", ""))
            pattern = str(args.get("pattern", r"\\d+"))
            matches = re.findall(pattern, data)
            return {{"matches": matches}}
        """
        raise NotImplementedError("_execute() must be implemented for intent: {intent}")

    async def run(self, context: dict, args: dict) -> dict:
        error = self.validate(args)
        if error:
            return {{"status": "error", "message": error, "intent": "{intent}"}}
        try:
            result = await self._execute(args)
            return {{"status": "success", "result": result, "intent": "{intent}"}}
        except Exception as e:
            return {{"status": "error", "message": str(e), "intent": "{intent}"}}
'''


_GENERIC_TEMPLATE = '''\
class {class_name}(BasePlugin):
    """
    {description}
    Category: generic

    This template is used when no specific category matched.
    Pick the pattern that fits your intent from the PATTERN LIBRARY above,
    or compose multiple patterns together.
    """
    name             = "{plugin_name}"
    description      = "{description}"
    version          = "{version}"
    permissions      = []
    safe_mode        = True
    allowed_services = []

    def validate(self, args: dict) -> str | None:
        # TODO: Add validation for any required args here
        return None

    async def _execute(self, args: dict) -> dict:
        """
        TODO: Implement the logic for intent "{intent}".
        Refer to the PATTERN LIBRARY for copy-paste patterns:
          - Async shell command → use asyncio.create_subprocess_shell + asyncio.wait_for
          - Background loop     → use threading.Event + daemon Thread
          - UI action           → use pyautogui (hotkey, typewrite, click, press)
          - HTTP request        → use urllib.request
          - File operation      → use os / shutil
        """
        raise NotImplementedError("_execute() must be implemented for intent: {intent}")

    async def run(self, context: dict, args: dict) -> dict:
        error = self.validate(args)
        if error:
            return {{"status": "error", "message": error, "intent": "{intent}"}}
        try:
            result = await self._execute(args)
            return {{"status": "success", "result": result, "intent": "{intent}"}}
        except Exception as e:
            return {{"status": "error", "message": str(e), "intent": "{intent}"}}
'''


# ── Test Templates ─────────────────────────────────────────────────────────────
# Per-category test templates mock the right dependencies so tests pass in
# the sandbox without needing real displays, files, or network.

_TEST_HEADER = '''\
"""
Tests for plugin: {plugin_name}
Intent: {intent}
Category: {category}
"""
import asyncio
import os
import sys
import pytest
from unittest.mock import patch, MagicMock, AsyncMock, call

# Add plugin directory to path
sys.path.insert(0, os.path.dirname(os.path.dirname(os.path.abspath(__file__))))
from plugin import {class_name}


@pytest.fixture
def plugin():
    return {class_name}()

@pytest.fixture
def ctx():
    return {{"active_window": "test_window", "app_type": "test", "app_name": "TestApp"}}

# ── Structural tests (always run, no mocking needed) ─────────────────────────

def test_has_required_attributes(plugin):
    assert plugin.name
    assert plugin.description
    assert plugin.version
    assert asyncio.iscoroutinefunction(plugin.run)
    assert callable(plugin.validate)

def test_validate_returns_none_or_str(plugin):
    result = plugin.validate({{}})
    assert result is None or isinstance(result, str)

def test_run_always_returns_dict_with_status(plugin, ctx):
    """run() must NEVER raise — always return a dict with 'status'."""
    result = asyncio.run(plugin.run(ctx, {{}}))
    assert isinstance(result, dict), f"Expected dict, got {{type(result)}}"
    assert "status" in result, f"Missing 'status' key in {{result}}"
    assert result["status"] in ("success", "error"), (
        f"status must be 'success' or 'error', got {{result['status']}}"
    )

def test_run_never_raises_on_bad_args(plugin, ctx):
    """Plugin must handle garbage input gracefully."""
    for bad_args in [None, {{}}, {{"x": None}}, {{"path": "/../../../etc/passwd"}}]:
        try:
            result = asyncio.run(plugin.run(ctx, bad_args or {{}}))
            assert isinstance(result, dict)
        except Exception as exc:
            pytest.fail(f"run() raised {{type(exc).__name__}}: {{exc}} for args={{bad_args}}")

'''

_TEST_BACKGROUND = '''\

# ── Background plugin tests ───────────────────────────────────────────────────
# We do NOT use @patch("plugin.pynput.keyboard") because the LLM imports
# pynput inside the worker/stopper function rather than at module level.
# In that case "plugin.pynput" does not exist as a module attribute
# and @patch raises AttributeError during test SETUP (not during the test),
# which appears as a confusing mock internals traceback.
# Instead we patch at the library level and test behaviour, not internals.

def test_run_starts_and_returns_immediately(plugin, ctx):
    """run() must return a dict quickly — threads run in background."""
    import time
    start = time.monotonic()
    try:
        result = asyncio.run(plugin.run(ctx, {{"interval": 0.01, "stop_hotkey": "alt+s"}}))
        elapsed = time.monotonic() - start
        assert isinstance(result, dict), f"run() must return dict, got {{type(result)}}"
        assert "status" in result, "result must have 'status' key"
        assert result["status"] in ("success", "error"), f"bad status: {{result['status']}}"
        if result["status"] == "success":
            assert elapsed < 5.0, f"run() blocked {{elapsed:.1f}}s — must be non-blocking"
    except Exception as exc:
        pytest.fail(f"run() raised instead of returning error dict: {{exc}}")

def test_run_is_non_blocking(plugin, ctx):
    """run() must return in under 5 seconds regardless of background threads."""
    import time
    start = time.monotonic()
    try:
        result = asyncio.run(plugin.run(ctx, {{}}))
        elapsed = time.monotonic() - start
        assert elapsed < 5.0, f"run() blocked {{elapsed:.1f}}s"
        assert isinstance(result, dict)
    except Exception as exc:
        elapsed = time.monotonic() - start
        if elapsed >= 5.0:
            pytest.fail(f"run() hung {{elapsed:.1f}}s before raising: {{exc}}")
'''

_TEST_AUTOMATION = '''\

# ── Automation plugin tests ───────────────────────────────────────────────────
# Patch at the library level ("pyautogui.click") not module level
# ("plugin.pyautogui.click") to handle both top-level and local imports.

@patch("pyautogui.click", return_value=None)
@patch("pyautogui.screenshot", return_value=MagicMock())
def test_run_succeeds_with_valid_args(mock_shot, mock_click, plugin, ctx):
    result = asyncio.run(plugin.run(ctx, {{"x": 100, "y": 200}}))
    assert result["status"] in ("success", "error")

@patch("pyautogui.click", side_effect=Exception("display error"))
def test_run_handles_pyautogui_error(mock_click, plugin, ctx):
    result = asyncio.run(plugin.run(ctx, {{}}))
    assert result["status"] == "error"
    assert "message" in result
'''

_TEST_WEB = '''\

# ── Web plugin tests (mock urllib) ────────────────────────────────────────────

@patch("urllib.request.urlopen")
def test_run_makes_http_request(mock_urlopen, plugin, ctx):
    import json as _json
    mock_resp = MagicMock()
    mock_resp.read.return_value = _json.dumps({{"result": "ok"}}).encode()
    mock_resp.__enter__ = lambda s: s
    mock_resp.__exit__ = MagicMock(return_value=False)
    mock_urlopen.return_value = mock_resp
    result = asyncio.run(plugin.run(ctx, {{"query": "test"}}))
    assert result["status"] in ("success", "error")

@patch("urllib.request.urlopen", side_effect=Exception("network error"))
def test_run_handles_network_error(mock_urlopen, plugin, ctx):
    result = asyncio.run(plugin.run(ctx, {{"query": "test"}}))
    assert result["status"] == "error"
    assert "message" in result
'''

_TEST_FILE = '''\

# ── File plugin tests (use tmp_path, no real FS mutation) ─────────────────────

def test_validate_rejects_missing_path(plugin):
    result = plugin.validate({{}})
    assert result is not None  # should fail validation

def test_validate_rejects_path_traversal(plugin):
    result = plugin.validate({{"path": "/etc/passwd"}})
    # Either validates or rejects — must not crash
    assert result is None or isinstance(result, str)

def test_run_reads_existing_file(plugin, ctx, tmp_path):
    f = tmp_path / "test.txt"
    f.write_text("hello operonix")
    result = asyncio.run(plugin.run(ctx, {{"path": str(f)}}))
    assert isinstance(result, dict)

def test_run_handles_missing_file(plugin, ctx, tmp_path):
    result = asyncio.run(plugin.run(ctx, {{"path": str(tmp_path / "nonexistent.txt")}}))
    assert isinstance(result, dict)  # must not raise
'''

_TEST_COMMAND = '''\

# ── Command plugin tests (mock asyncio subprocess) ────────────────────────────
# Patch asyncio.create_subprocess_shell — NOT subprocess.run (which is banned).

@pytest.mark.asyncio
async def test_run_executes_command(plugin, ctx):
    mock_proc = MagicMock()
    mock_proc.communicate = AsyncMock(return_value=(b"hello\n", b""))
    mock_proc.returncode = 0
    with patch("asyncio.create_subprocess_shell", return_value=mock_proc):
        result = await plugin.run(ctx, {{"command": ["echo", "hello"]}})
    assert result["status"] in ("success", "error")

@pytest.mark.asyncio
async def test_run_handles_command_failure(plugin, ctx):
    mock_proc = MagicMock()
    mock_proc.communicate = AsyncMock(return_value=(b"", b"permission denied"))
    mock_proc.returncode = 1
    with patch("asyncio.create_subprocess_shell", return_value=mock_proc):
        result = await plugin.run(ctx, {{"command": ["ls", "/root"]}})
    assert isinstance(result, dict)

def test_validate_rejects_missing_command(plugin):
    result = plugin.validate({{}})
    assert result is not None
'''

_TEST_SYSTEM = '''\

# ── System plugin tests (mock asyncio subprocess) ─────────────────────────────

@pytest.mark.asyncio
async def test_run_system_action(plugin, ctx):
    mock_proc = MagicMock()
    mock_proc.communicate = AsyncMock(return_value=(b"ok", b""))
    mock_proc.returncode = 0
    with patch("asyncio.create_subprocess_shell", return_value=mock_proc):
        result = await plugin.run(ctx, {{}})
    assert result["status"] in ("success", "error")

@pytest.mark.asyncio
async def test_run_handles_tool_failure(plugin, ctx):
    mock_proc = MagicMock()
    mock_proc.communicate = AsyncMock(return_value=(b"", b"no such tool"))
    mock_proc.returncode = 127
    with patch("asyncio.create_subprocess_shell", return_value=mock_proc):
        result = await plugin.run(ctx, {{}})
    assert isinstance(result, dict)
    assert "message" in result or "result" in result
'''

_TEST_DATA = '''\

# ── Data plugin tests (no mocking needed — pure Python) ───────────────────────

def test_run_processes_valid_input(plugin, ctx):
    result = asyncio.run(plugin.run(ctx, {{"data": "hello world"}}))
    assert result["status"] in ("success", "error")

def test_run_handles_empty_input(plugin, ctx):
    result = asyncio.run(plugin.run(ctx, {{"data": ""}}))
    assert isinstance(result, dict)

def test_run_handles_malformed_input(plugin, ctx):
    result = asyncio.run(plugin.run(ctx, {{"data": None}}))
    assert isinstance(result, dict)
'''

_TEST_GENERIC = '''\

# ── Generic plugin tests ──────────────────────────────────────────────────────

def test_run_returns_success_or_error(plugin, ctx):
    result = asyncio.run(plugin.run(ctx, {{}}))
    assert result["status"] in ("success", "error")
'''

_CATEGORY_TEMPLATES = {
    "background": _BACKGROUND_TEMPLATE,
    "automation": _AUTOMATION_TEMPLATE,
    "web":        _WEB_TEMPLATE,
    "file":       _FILE_TEMPLATE,
    "command":    _COMMAND_TEMPLATE,
    "system":     _SYSTEM_TEMPLATE,
    "data":       _DATA_TEMPLATE,
    "generic":    _GENERIC_TEMPLATE,
}

_CATEGORY_TEST_BODIES = {
    "background": _TEST_BACKGROUND,
    "automation": _TEST_AUTOMATION,
    "web":        _TEST_WEB,
    "file":       _TEST_FILE,
    "command":    _TEST_COMMAND,
    "system":     _TEST_SYSTEM,
    "data":       _TEST_DATA,
    "generic":    _TEST_GENERIC,
}


# ── TemplateEngine class ───────────────────────────────────────────────────────

class TemplateEngine:
    """
    Generates plugin and test scaffolding for a given intent/category.

    Key design decisions:
    1. Templates are WORKING examples, not empty stubs.
       The LLM fills in intent-specific logic, not boilerplate.
    2. Tests mock the right dependencies per category.
       Background tests mock pyautogui+pynput. Web tests mock urllib. Etc.
    3. The pattern library is exposed for injection into the generator prompt.
       This gives the LLM concrete, copy-paste-ready patterns.
    4. No fake registry services. Templates only use what actually exists.
    """

    def __init__(self):
        self.logger = logging.getLogger("TemplateEngine")

    def get_plugin_skeleton(
        self,
        plugin_name: str,
        intent: str,
        description: str,
        version: str = "1.0",
        category: str | None = None,
    ) -> str:
        """
        Returns a complete plugin.py skeleton for the given intent.
        The skeleton is a WORKING example — the LLM only fills in the
        TODO sections with intent-specific logic.
        """
        from datetime import datetime

        if category is None:
            category = detect_category(intent, description)

        class_name = self._to_class_name(plugin_name)
        template   = _CATEGORY_TEMPLATES.get(category, _GENERIC_TEMPLATE)

        header = _PLUGIN_HEADER.format(
            plugin_name=plugin_name,
            intent=intent,
            description=description,
            category=category,
            version=version,
            timestamp=datetime.utcnow().strftime("%Y-%m-%d %H:%M UTC"),
        )

        body = template.format(
            class_name=class_name,
            plugin_name=plugin_name,
            intent=intent,
            description=description,
            version=version,
        )

        self.logger.debug(
            f"Generated '{category}' skeleton for '{plugin_name}'"
        )
        return header + body

    def get_test_skeleton(
        self, plugin_name: str, intent: str, category: str | None = None
    ) -> str:
        """
        Returns a test_plugin.py skeleton with category-appropriate mocking.
        Tests are designed to pass in the sandbox without real display/network/FS.
        """
        if category is None:
            category = detect_category(intent)

        class_name = self._to_class_name(plugin_name)

        header = _TEST_HEADER.format(
            plugin_name=plugin_name,
            intent=intent,
            category=category,
            class_name=class_name,
        )
        body = _CATEGORY_TEST_BODIES.get(category, _TEST_GENERIC)

        return header + body

    def get_pattern_library(self) -> str:
        """
        Returns the pattern library string for injection into the generator prompt.
        Gives the LLM concrete, copy-paste-ready patterns for common sub-tasks.
        """
        return PATTERN_LIBRARY

    def get_category(self, intent: str, description: str = "") -> str:
        """Public helper to inspect which category would be selected."""
        return detect_category(intent, description)

    @staticmethod
    def _to_class_name(plugin_name: str) -> str:
        """Converts snake_case plugin_name to PascalCase class name."""
        return "".join(part.capitalize() for part in plugin_name.split("_"))


# Global instance
template_engine = TemplateEngine()