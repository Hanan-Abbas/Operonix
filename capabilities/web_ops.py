# capabilities/web_ops.py
import asyncio

# -------------------------
# Web / Browser Operation Capabilities
# -------------------------

async def open_url(context, args):
    """
    Prepare opening a URL in the browser.
    args: {"url": str}
    """
    return {
        "intent": "open_url",
        "capabilities": ["browser_open"],
        "args": args
    }

async def click_link(context, args):
    """
    Prepare clicking a link on a web page.
    args: {"selector": str, "url": str | optional}
    """
    return {
        "intent": "click_link",
        "capabilities": ["browser_click"],
        "args": args
    }

async def fill_form(context, args):
    """
    Prepare filling a form on a web page.
    args: {"selector": str, "values": dict, "url": str | optional}
    """
    return {
        "intent": "fill_form",
        "capabilities": ["browser_form_fill"],
        "args": args
    }

async def submit_form(context, args):
    """
    Prepare submitting a form on a web page.
    args: {"selector": str, "url": str | optional}
    """
    return {
        "intent": "submit_form",
        "capabilities": ["browser_form_submit"],
        "args": args
    }

async def extract_text(context, args):
    """
    Prepare extracting text content from a webpage.
    args: {"selector": str, "url": str | optional}
    """
    return {
        "intent": "extract_text",
        "capabilities": ["browser_extract_text"],
        "args": args
    }

async def screenshot(context, args):
    """
    Prepare taking a screenshot of a webpage or element.
    args: {"selector": str | optional, "url": str | optional, "path": str}
    """
    return {
        "intent": "screenshot",
        "capabilities": ["browser_screenshot"],
        "args": args
    }

# -------------------------
# Optional helper for validation
# -------------------------
async def validate_url(args):
    """
    Example helper validation: check if URL exists
    """
    url = args.get("url")
    if not url or not url.startswith(("http://", "https://")):
        return False, "Invalid URL"
    return True, None
