"""
capabilities/file_ops.py
─────────────────────────
File-system capabilities.

Changes from original
──────────────────────
1. Every capability now *executes* the operation (os.makedirs, shutil.move,
   etc.) instead of just returning a descriptor dict.  The executor's
   capability_registry.execute() call will get a real result.

2. Each capability returns a normalised dict:
       {"success": bool, "result": <value>, "intent": <name>}
   The executor checks the "success" key and surfaces the result.

3. `create_dir` resolves the directory path from context before executing:
   - If args contain an explicit "path", use it.
   - If args contain "location": "current window", read the active window's
     working directory from context["cwd"] (set by window_detector) and
     join dir_name to it.
   - Falls back to the OS home directory so it never silently no-ops.

4. CAPABILITY_METADATA dict — read by CapabilityMapper._resolve_suggested_tool()
   so that suggested_tool is populated correctly and the DecisionEngine does
   not fall through to the wrong "api_tool" catch-all.

5. safe_path_check now accepts the normalised "path" key that create_dir and
   others produce after argument normalisation.
"""
from __future__ import annotations

import os
import shutil
from pathlib import Path

# ── Metadata consumed by CapabilityMapper._resolve_suggested_tool() ────── #
# "tool"   → the tool_type string registered in ToolRegistry
# "method" → the waterfall tier (plugin | api | command | ui)
CAPABILITY_METADATA: dict[str, dict] = {
    "write_file":   {"tool": "file_tool",  "method": "api"},
    "append_file":  {"tool": "file_tool",  "method": "api"},
    "read_file":    {"tool": "file_tool",  "method": "api"},
    "delete_file":  {"tool": "file_tool",  "method": "api"},
    "move_file":    {"tool": "file_tool",  "method": "api"},
    "list_dir":     {"tool": "file_tool",  "method": "api"},
    "create_dir":   {"tool": "shell_tool", "method": "command"},
    "delete_dir":   {"tool": "shell_tool", "method": "command"},
}


# ── Path resolver ─────────────────────────────────────────────────────── #

def _resolve_path(args: dict, context: dict) -> str:
    """
    Determine the real filesystem path for a file/dir operation.

    Resolution order:
      1. Explicit "path" in args — use as-is.
      2. "dir_name" + location hint "current window" -> context["cwd"] / dir_name.
      3. "dir_name" alone -> home directory / dir_name.
      4. Fallback: current working directory.
    """
    explicit = args.get("path", "").strip()
    if explicit:
        return explicit

    dir_name = args.get("dir_name", "").strip()
    location = str(args.get("location", "")).lower()

    if dir_name:
        if "current window" in location or "current" in location:
            # window_detector puts the focused window's CWD in context
            cwd = context.get("cwd") or context.get("window_cwd") or os.getcwd()
            return str(Path(cwd) / dir_name)
        return str(Path.home() / dir_name)

    return os.getcwd()


# ── Capabilities ──────────────────────────────────────────────────────── #

async def write_file(context: dict, args: dict) -> dict:
    """Write content to a file.  args: {path, content, mode="w"}"""
    path = _resolve_path(args, context)
    content = args.get("content", args.get("data", ""))
    mode = args.get("mode", "w")
    try:
        Path(path).parent.mkdir(parents=True, exist_ok=True)
        with open(path, mode, encoding="utf-8") as fh:
            fh.write(content)
        return {"success": True, "result": f"Written to {path}", "intent": "write_file"}
    except Exception as exc:
        return {"success": False, "result": str(exc), "intent": "write_file"}


async def append_file(context: dict, args: dict) -> dict:
    """Append content to a file.  args: {path, content}"""
    return await write_file(context, {**args, "mode": "a"})


async def read_file(context: dict, args: dict) -> dict:
    """Read file contents.  args: {path}"""
    path = _resolve_path(args, context)
    try:
        with open(path, "r", encoding="utf-8") as fh:
            data = fh.read()
        return {"success": True, "result": data, "intent": "read_file"}
    except Exception as exc:
        return {"success": False, "result": str(exc), "intent": "read_file"}


async def delete_file(context: dict, args: dict) -> dict:
    """Delete a file.  args: {path}"""
    path = _resolve_path(args, context)
    try:
        os.remove(path)
        return {"success": True, "result": f"Deleted {path}", "intent": "delete_file"}
    except Exception as exc:
        return {"success": False, "result": str(exc), "intent": "delete_file"}


async def move_file(context: dict, args: dict) -> dict:
    """Move/rename a file.  args: {src, dst}"""
    src = args.get("src") or args.get("path", "")
    dst = args.get("dst") or args.get("destination", "")
    try:
        shutil.move(src, dst)
        return {"success": True, "result": f"Moved {src} to {dst}", "intent": "move_file"}
    except Exception as exc:
        return {"success": False, "result": str(exc), "intent": "move_file"}


async def list_dir(context: dict, args: dict) -> dict:
    """List directory contents.  args: {path}"""
    path = _resolve_path(args, context)
    try:
        entries = os.listdir(path)
        return {"success": True, "result": entries, "intent": "list_dir"}
    except Exception as exc:
        return {"success": False, "result": str(exc), "intent": "list_dir"}


async def create_dir(context: dict, args: dict) -> dict:
    """
    Create a directory.

    args accepted (any combination):
      path       - explicit absolute/relative path
      dir_name   - directory name; resolved against context["cwd"] when
                   location=="current window", else against home dir
      exist_ok   - bool (default True)
      location   - "current window" | any hint string
    """
    path = _resolve_path(args, context)
    exist_ok = bool(args.get("exist_ok", True))
    try:
        os.makedirs(path, exist_ok=exist_ok)
        return {"success": True, "result": f"Directory created: {path}", "intent": "create_dir"}
    except FileExistsError:
        return {"success": False, "result": f"Already exists: {path}", "intent": "create_dir"}
    except Exception as exc:
        return {"success": False, "result": str(exc), "intent": "create_dir"}


async def delete_dir(context: dict, args: dict) -> dict:
    """Delete a directory.  args: {path, recursive=False}"""
    path = _resolve_path(args, context)
    recursive = bool(args.get("recursive", False))
    try:
        if recursive:
            shutil.rmtree(path)
        else:
            os.rmdir(path)
        return {"success": True, "result": f"Directory deleted: {path}", "intent": "delete_dir"}
    except Exception as exc:
        return {"success": False, "result": str(exc), "intent": "delete_dir"}


# ── Validation helpers (used by tool_validator / bootstrap) ──────────── #

async def safe_path_check(context: dict, args: dict) -> tuple:
    """
    Returns (True, None) if the path is safe to operate on.
    Accepts "path" OR the dir_name+location combo create_dir uses.
    """
    path = _resolve_path(args, context)
    if not path:
        return False, "Missing path"
    if ".." in path:
        return False, f"Unsafe path - contains '..': {path!r}"
    forbidden_roots = {"/etc", "/boot", "/sys", "/proc", "C:\\Windows"}
    for root in forbidden_roots:
        if path.startswith(root):
            return False, f"Forbidden system path: {path!r}"
    return True, None