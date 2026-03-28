# capabilities/text_ops.py
import asyncio

# -------------------------
# Text / Code Operation Capabilities
# -------------------------

async def generate_text(context, args):
    """
    Prepare AI-generated text.
    args: {"prompt": str, "max_tokens": int | optional, "style": str | optional}
    """
    return {
        "intent": "generate_text",
        "capabilities": ["text_generate"],
        "args": args
    }

async def summarize_text(context, args):
    """
    Prepare text summarization.
    args: {"text": str, "max_length": int | optional}
    """
    return {
        "intent": "summarize_text",
        "capabilities": ["text_summarize"],
        "args": args
    }

async def translate_text(context, args):
    """
    Prepare text translation.
    args: {"text": str, "target_language": str}
    """
    return {
        "intent": "translate_text",
        "capabilities": ["text_translate"],
        "args": args
    }

async def correct_grammar(context, args):
    """
    Prepare grammar correction.
    args: {"text": str, "language": str | optional}
    """
    return {
        "intent": "correct_grammar",
        "capabilities": ["text_grammar_correct"],
        "args": args
    }

async def code_generate(context, args):
    """
    Prepare AI-generated code snippet.
    args: {"prompt": str, "language": str | optional, "max_lines": int | optional}
    """
    return {
        "intent": "code_generate",
        "capabilities": ["code_generate"],
        "args": args
    }

async def code_format(context, args):
    """
    Prepare code formatting operation.
    args: {"code": str, "language": str | optional, "style": str | optional}
    """
    return {
        "intent": "code_format",
        "capabilities": ["code_format"],
        "args": args
    }

async def code_analyze(context, args):
    """
    Prepare code analysis operation.
    args: {"code": str, "language": str | optional}
    """
    return {
        "intent": "code_analyze",
        "capabilities": ["code_analyze"],
        "args": args
    }

# -------------------------
# Optional validation helpers
# -------------------------
async def validate_text(args):
    """
    Example helper: ensures text or prompt exists
    """
    text = args.get("text") or args.get("prompt") or args.get("code")
    if not text or not isinstance(text, str):
        return False, "Missing or invalid text/prompt/code"
    return True, None
