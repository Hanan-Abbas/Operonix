# capabilities/file_ops.py
import asyncio
import os

# -------------------------
# File Operation Capabilities
# -------------------------

async def write_file(context, args):
    """
    Prepare write_file action
    args: {"path": str, "content": str, "mode": str = "w"}
    """
    return {
        "intent": "write_file",
        "capabilities": ["file_write"],
        "args": args
    }

async def append_file(context, args):
    """
    Prepare append_file action
    args: {"path": str, "content": str}
    """
    return {
        "intent": "append_file",
        "capabilities": ["file_append"],
        "args": args
    }

async def read_file(context, args):
    """
    Prepare read_file action
    args: {"path": str}
    """
    return {
        "intent": "read_file",
        "capabilities": ["file_read"],
        "args": args
    }

async def delete_file(context, args):
    """
    Prepare delete_file action
    args: {"path": str}
    """
    return {
        "intent": "delete_file",
        "capabilities": ["file_delete"],
        "args": args
    }

async def move_file(context, args):
    """
    Prepare move_file action
    args: {"src": str, "dst": str}
    """
    return {
        "intent": "move_file",
        "capabilities": ["file_move"],
        "args": args
    }

async def list_dir(context, args):
    """
    Prepare list_dir action
    args: {"path": str}
    """
    return {
        "intent": "list_dir",
        "capabilities": ["file_list"],
        "args": args
    }

async def create_dir(context, args):
    """
    Prepare create_dir action
    args: {"path": str, "exist_ok": bool = True}
    """
    return {
        "intent": "create_dir",
        "capabilities": ["directory_create"],
        "args": args
    }

async def delete_dir(context, args):
    """
    Prepare delete_dir action
    args: {"path": str, "recursive": bool = False}
    """
    return {
        "intent": "delete_dir",
        "capabilities": ["directory_delete"],
        "args": args
    }

# -------------------------
# Example: Validation-Friendly Helpers
# -------------------------
async def safe_path_check(context, args):
    """
    Returns True if path is safe
    Could be used as a validation rule
    """
    path = args.get("path")
    if not path:
        return False, "Missing path"
    if ".." in path:
        return False, "Unsafe path: contains '..'"
    return True, None
