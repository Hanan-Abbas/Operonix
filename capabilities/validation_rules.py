# capabilities/validation_rules.py
import asyncio
import os
from capabilities.file_ops import safe_path_check
from capabilities.text_ops import validate_text
from capabilities.web_ops import validate_url
from capabilities.ui_ops import validate_coordinates
from capabilities.command_ops import validate_command

# -------------------------
# Centralized Validation Rules
# -------------------------

# Each validation is an async function that returns (bool, error_message)
# True = valid, False = invalid

async def validate_file_exists(args):
    """
    Validates that the specified file exists.
    args: {"path": str}
    """
    path = args.get("path")
    if not path:
        return False, "Missing path"
    if not os.path.isfile(path):
        return False, f"File not found: {path}"
    return True, None

async def validate_dir_exists(args):
    """
    Validates that the specified directory exists.
    args: {"path": str}
    """
    path = args.get("path")
    if not path:
        return False, "Missing path"
    if not os.path.isdir(path):
        return False, f"Directory not found: {path}"
    return True, None

async def validate_safe_path(args):
    """
    Ensures paths are safe (no '..' or suspicious characters).
    Reuses helper from file_ops
    """
    return await safe_path_check({}, args)

async def validate_text_nonempty(args):
    """
    Ensures text/code/prompt exists and is non-empty.
    """
    return await validate_text(args)

async def validate_url_format(args):
    """
    Ensures a valid URL.
    """
    return await validate_url(args)

async def validate_ui_coordinates(args):
    """
    Ensures x,y coordinates are present and numeric.
    """
    return await validate_coordinates(args)

async def validate_command_safe(args):
    """
    Ensures command/script is present and safe.
    """
    return await validate_command(args)

# -------------------------
# Register All Validation Rules
# -------------------------
# When registry imports this module, it can loop through and add all
all_validation_rules = [
    validate_file_exists,
    validate_dir_exists,
    validate_safe_path,
    validate_text_nonempty,
    validate_url_format,
    validate_ui_coordinates,
    validate_command_safe
]
