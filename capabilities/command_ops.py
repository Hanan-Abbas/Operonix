# capabilities/command_ops.py
import asyncio

# -------------------------
# Shell / Command Operation Capabilities
# -------------------------

async def run_command(context, args):
    """
    Prepare running a shell/terminal command.
    args: {"command": str, "cwd": str | optional, "capture_output": bool = True}
    """
    return {
        "intent": "run_command",
        "capabilities": ["shell_execute"],
        "args": args
    }

async def git_op(context, args):
    """
    Prepare a Git operation.
    args: {"operation": "commit/push/pull/status", "options": dict}
    """
    return {
        "intent": "git_op",
        "capabilities": ["git_execute"],
        "args": args
    }

async def install_package(context, args):
    """
    Prepare installing a package via package manager.
    args: {"package_name": str, "manager": str | optional, "global": bool | optional}
    """
    return {
        "intent": "install_package",
        "capabilities": ["package_install"],
        "args": args
    }

async def check_status(context, args):
    """
    Prepare checking system or app status.
    args: {"service": str | optional, "process": str | optional}
    """
    return {
        "intent": "check_status",
        "capabilities": ["system_status"],
        "args": args
    }

async def execute_script(context, args):
    """
    Prepare executing a script file.
    args: {"script_path": str, "interpreter": str | optional}
    """
    return {
        "intent": "execute_script",
        "capabilities": ["script_execute"],
        "args": args
    }

# -------------------------
# Optional validation helper
# -------------------------
async def validate_command(args):
    """
    Basic check: shell command, script path, git operation, or status target.
    """
    command = args.get("command") or args.get("script_path")
    if command and isinstance(command, str):
        return True, None
    if args.get("operation") and isinstance(args.get("operation"), str):
        return True, None
    if args.get("service") or args.get("process"):
        return True, None
    return False, "Command, script path, git operation, or status target must be provided"
