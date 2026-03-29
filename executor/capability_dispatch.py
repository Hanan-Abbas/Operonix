"""
Maps capability intents (registry function names) to tool calls.
"""
from __future__ import annotations

import platform
import shlex
from typing import Any, Dict, Optional, Tuple
from urllib.parse import quote_plus


def _copy(a: Dict[str, Any]) -> Dict[str, Any]:
    return dict(a) if a else {}


def _open_url_command(url: str) -> str:
    u = shlex.quote(url)
    if platform.system() == "Windows":
        return f"start \"\" {u}"
    if platform.system() == "Darwin":
        return f"open {u}"
    return f"xdg-open {u}"


def resolve_tool_call(intent: str, args: Dict[str, Any]) -> Optional[Tuple[str, str, Dict[str, Any]]]:
    """
    Returns (tool_name, tool_action, normalized_args) or None if unknown.
    """
    a = _copy(args)

    if intent == "write_file":
        return "file_tool", "write", {"path": a.get("path"), "data": a.get("content", "")}

    if intent == "append_file":
        return "file_tool", "append", {"path": a.get("path"), "data": a.get("content", "")}

    if intent == "read_file":
        return "file_tool", "read", {"path": a.get("path")}

    if intent == "delete_file":
        return "file_tool", "delete", {"path": a.get("path")}

    if intent == "move_file":
        return (
            "file_tool",
            "move",
            {"path": a.get("src") or a.get("path"), "destination": a.get("dst") or a.get("destination")},
        )

    if intent == "list_dir":
        return "file_tool", "list", {"path": a.get("path", ".")}

    if intent == "create_dir":
        return "file_tool", "mkdir", {"path": a.get("path"), "exist_ok": a.get("exist_ok", True)}

    if intent == "delete_dir":
        return "file_tool", "delete", {"path": a.get("path")}

    if intent == "run_command":
        return "shell_tool", "execute", {"command": a.get("command", "")}

    if intent == "install_package":
        pkg = a.get("package_name", "")
        manager = (a.get("manager") or "apt").lower()
        if manager in ("apt", "apt-get"):
            cmd = f"sudo apt-get install -y {shlex.quote(pkg)}"
        elif manager in ("pip", "pip3"):
            cmd = f"{manager} install {shlex.quote(pkg)}"
        elif manager == "npm":
            cmd = f"npm install -g {shlex.quote(pkg)}"
        else:
            cmd = str(a.get("command") or f"{manager} {pkg}")
        return "shell_tool", "execute", {"command": cmd}

    if intent == "git_op":
        op = str(a.get("operation", "status")).strip()
        return "shell_tool", "execute", {"command": f"git {op}"}

    if intent == "execute_script":
        sp = shlex.quote(str(a.get("script_path", "")))
        interp = str(a.get("interpreter") or "").strip()
        cmd = f"{interp} {sp}".strip() if interp else sp.strip()
        return "shell_tool", "execute", {"command": cmd}

    if intent == "check_status":
        svc = a.get("service") or a.get("process") or ""
        if not svc:
            return "shell_tool", "execute", {"command": "echo ok"}
        return "shell_tool", "execute", {"command": f"pgrep -af {shlex.quote(str(svc))}"}

    if intent == "type_text":
        return "ui_tool", "type", {"text": a.get("text", ""), "interval": a.get("interval", 0.05)}

    if intent == "click":
        return "ui_tool", "click", {"x": a.get("x"), "y": a.get("y"), "clicks": a.get("clicks", 1)}

    if intent == "double_click":
        return "ui_tool", "click", {"x": a.get("x"), "y": a.get("y"), "clicks": 2}

    if intent == "move_cursor":
        return "ui_tool", "move", {"x": a.get("x"), "y": a.get("y")}

    if intent == "scroll":
        return "ui_tool", "scroll", {"direction": a.get("direction", "down"), "amount": a.get("amount", 3)}

    if intent == "navigate":
        path = a.get("path") or a.get("url")
        if not path:
            return None
        return "shell_tool", "execute", {"command": _open_url_command(str(path))}

    if intent == "open_url":
        url = a.get("url", "")
        return "shell_tool", "execute", {"command": _open_url_command(url)}

    if intent == "search_web":
        q = a.get("query") or a.get("q") or ""
        url = f"https://www.google.com/search?q={quote_plus(str(q))}"
        return "shell_tool", "execute", {"command": _open_url_command(url)}

    if intent in ("extract_text", "fill_form", "submit_form", "click_link"):
        target = a.get("url")
        if not target:
            return None
        return "api_tool", "request", {"url": target, "method": "GET", "data": {}, "headers": {}}

    if intent == "screenshot":
        path = a.get("path") or "screenshot.png"
        if a.get("url"):
            return "shell_tool", "execute", {"command": _open_url_command(str(a["url"]))}
        return "ui_tool", "screenshot", {"path": path}

    return None
