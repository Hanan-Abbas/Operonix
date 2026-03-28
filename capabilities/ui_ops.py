# capabilities/ui_ops.py
import asyncio

# -------------------------
# UI / Desktop Automation Capabilities
# -------------------------

async def click(context, args):
    """
    Prepare a UI click action.
    args: {"x": int, "y": int, "window": str | optional}
    """
    return {
        "intent": "click",
        "capabilities": ["ui_click"],
        "args": args
    }

async def double_click(context, args):
    """
    Prepare a UI double-click action.
    args: {"x": int, "y": int, "window": str | optional}
    """
    return {
        "intent": "double_click",
        "capabilities": ["ui_double_click"],
        "args": args
    }

async def type_text(context, args):
    """
    Prepare typing action in a window or text field.
    args: {"text": str, "window": str | optional}
    """
    return {
        "intent": "type_text",
        "capabilities": ["ui_type"],
        "args": args
    }

async def move_cursor(context, args):
    """
    Prepare moving the mouse cursor.
    args: {"x": int, "y": int}
    """
    return {
        "intent": "move_cursor",
        "capabilities": ["ui_move_cursor"],
        "args": args
    }

async def scroll(context, args):
    """
    Prepare a scroll action.
    args: {"direction": "up/down/left/right", "amount": int, "window": str | optional}
    """
    return {
        "intent": "scroll",
        "capabilities": ["ui_scroll"],
        "args": args
    }

async def navigate(context, args):
    """
    Prepare UI navigation.
    args: {"path": str, "window": str | optional}
    """
    return {
        "intent": "navigate",
        "capabilities": ["ui_navigate"],
        "args": args
    }

# -------------------------
# Optional validation helpers
# -------------------------
async def validate_coordinates(args):
    """
    Example helper: ensure x, y coordinates are numbers
    """
    x = args.get("x")
    y = args.get("y")
    if x is None or y is None:
        return False, "Missing x or y coordinate"
    if not isinstance(x, (int, float)) or not isinstance(y, (int, float)):
        return False, "Coordinates must be numbers"
    return True, None
