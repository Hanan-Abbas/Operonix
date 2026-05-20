"""
tools/payload_serializers.py
─────────────────────────────
Four pure serializer functions — one per execution layer.

Called once by MethodRouter at routing time to fill all LayeredPayload
slots simultaneously.  Never called at execution time.

Contract
────────
• Each function receives the canonical ParsedIntent dict produced by
  IntentParser._parse_async() and returns a plain dict or list that
  LayeredPayload's __post_init__ will deep-freeze into an immutable form.
• No side effects, no I/O, no imports of runtime singletons at module
  level — these are pure transformations.
• If a required value is absent from the intent, the function returns
  a minimal but valid structure so the executor can surface a clear
  error rather than a KeyError.

ParsedIntent shape (from brain/intent_parser.py)
──────────────────────────────────────────────────
{
    "intent"      : str | None,   # snake_case capability name
    "confidence"  : float,        # 0.0 – 1.0
    "parameters"  : dict,         # arbitrary key-value pairs from the LLM
    "profile_hint": str | None,   # "ghost" | "bridge" | "lab" | None
}
"""

from __future__ import annotations

import logging
import shlex
from typing import Any

logger = logging.getLogger("PayloadSerializers")


# ─────────────────────────────────────────────────────────────────────────────
# Internal helpers
# ─────────────────────────────────────────────────────────────────────────────

def _get_parameters(intent: dict[str, Any]) -> dict[str, Any]:
    """
    Safe accessor for intent["parameters"].  Always returns a dict even
    if the key is absent or the value is None.
    """
    params = intent.get("parameters")
    if not isinstance(params, dict):
        return {}
    return params


def _intent_name(intent: dict[str, Any]) -> str:
    """Return the intent name or an empty string if absent."""
    return str(intent.get("intent") or "")


def _build_command_string(intent_name: str, parameters: dict[str, Any]) -> str:
    """
    Reconstruct a shell command string from the intent name and parameters
    for intents that map directly to shell operations.

    Priority order for the command string:
      1. parameters["command"]   — explicit command override from the LLM
      2. parameters["cmd"]       — short alias
      3. intent_name with underscores replaced by spaces — last resort

    Arguments are read from parameters["args"], which may be a list of
    strings or a single string.  Either form is safe-quoted via shlex.
    """
    cmd = (
        parameters.get("command")
        or parameters.get("cmd")
        or intent_name.replace("_", " ")
    )
    cmd = str(cmd).strip()

    raw_args = parameters.get("args", [])
    if isinstance(raw_args, list):
        arg_tokens = [str(a) for a in raw_args]
    elif isinstance(raw_args, str) and raw_args.strip():
        arg_tokens = shlex.split(raw_args)
    else:
        arg_tokens = []

    # Additional single-value parameters that may carry positional context
    for key in ("path", "target", "file_path", "directory", "url", "query"):
        val = parameters.get(key)
        if val and str(val).strip() not in arg_tokens:
            arg_tokens.append(str(val).strip())

    if arg_tokens:
        return f"{cmd} {' '.join(arg_tokens)}"
    return cmd


# ─────────────────────────────────────────────────────────────────────────────
# Serializer 1 — Plugin layer
# ─────────────────────────────────────────────────────────────────────────────

def to_plugin_kwargs(intent: dict[str, Any]) -> dict[str, Any]:
    """
    Produce the keyword-argument dict passed to BasePlugin.run(context, args).

    BasePlugin.run() signature (from plugins/manifest_schema.py):
        async def run(self, context: dict, args: dict) -> dict

    The router passes the returned dict as *args*.  All LLM-extracted
    parameters are forwarded verbatim, plus two housekeeping keys the
    plugin sandbox expects:
        _intent       — the canonical intent name
        _profile_hint — execution profile from the intent parser
    """
    params = _get_parameters(intent)

    kwargs: dict[str, Any] = {}
    kwargs.update(params)                              # all LLM parameters
    kwargs["_intent"]        = _intent_name(intent)   # routing metadata
    kwargs["_profile_hint"]  = intent.get("profile_hint")

    # Normalize common aliases so plugins receive a consistent interface
    if "file_path" in kwargs and "path" not in kwargs:
        kwargs["path"] = kwargs["file_path"]
    if "directory" in kwargs and "path" not in kwargs:
        kwargs["path"] = kwargs["directory"]

    return kwargs


# ─────────────────────────────────────────────────────────────────────────────
# Serializer 2 — API layer
# ─────────────────────────────────────────────────────────────────────────────

def to_api_body(intent: dict[str, Any]) -> dict[str, Any]:
    """
    Produce the JSON payload body for APITool.run(action, args).

    APITool.run() signature (from tools/api_tool.py):
        async def run(self, action: str, args: dict)

    Where args may contain:
        url     — target URL
        method  — HTTP verb (GET / POST / PUT / DELETE)
        data    — request body
        headers — HTTP headers

    The intent name is also forwarded as "action" so the API tool can
    log which intent triggered the call without the executor having to
    inject it separately.
    """
    params = _get_parameters(intent)

    body: dict[str, Any] = {}

    # Direct pass-through of API-relevant parameters
    for key in ("url", "method", "data", "headers", "endpoint", "payload"):
        val = params.get(key)
        if val is not None:
            body[key] = val

    # Default HTTP method to GET when not specified
    if "method" not in body:
        body["method"] = "GET"

    # If a "data" or "payload" key exists, treat it as the POST body
    if "payload" in body and "data" not in body:
        body["data"] = body.pop("payload")

    # Forward remaining parameters as query-string candidates
    remaining = {
        k: v for k, v in params.items()
        if k not in ("url", "method", "data", "headers", "endpoint", "payload")
    }
    if remaining:
        body["params"] = remaining

    # Routing metadata
    body["_intent"]       = _intent_name(intent)
    body["_profile_hint"] = intent.get("profile_hint")

    return body


# ─────────────────────────────────────────────────────────────────────────────
# Serializer 3 — Shell layer
# ─────────────────────────────────────────────────────────────────────────────

def to_shell_argv(intent: dict[str, Any]) -> list[str]:
    """
    Produce the tokenized argv list for ShellTool.

    subprocess.run() accepts a sequence of strings directly (no shell
    injection risk when passed as a list).  LayeredPayload converts this
    list to a tuple[str, ...] in __post_init__ for immutability.

    Construction strategy
    ─────────────────────
    1. If parameters["command"] is already a full command string, tokenize
       it with shlex.split() (safe; does not invoke a shell).
    2. Otherwise, reconstruct the command from the intent name and any
       positional parameters ("args", "path", "target", etc.).

    The profile_hint is NOT injected into argv — it is forwarded in the
    LayeredPayload's ui_action as metadata for the terminal resolver.
    The executor reads profile_hint separately from the intent dict.
    """
    params = _get_parameters(intent)

    raw_command: str | None = params.get("command") or params.get("cmd")

    if raw_command:
        raw_command = str(raw_command).strip()
        try:
            tokens = shlex.split(raw_command)
        except ValueError:
            # shlex.split fails on unmatched quotes; fall back to whitespace split
            logger.warning(
                "shlex.split failed for command %r — falling back to str.split()",
                raw_command,
            )
            tokens = raw_command.split()
    else:
        # Reconstruct from intent and parameters
        full_cmd = _build_command_string(_intent_name(intent), params)
        try:
            tokens = shlex.split(full_cmd)
        except ValueError:
            tokens = full_cmd.split()

    # Ensure every token is a plain string (guard against numeric LLM outputs)
    return [str(t) for t in tokens if t]


# ─────────────────────────────────────────────────────────────────────────────
# Serializer 4 — UI layer
# ─────────────────────────────────────────────────────────────────────────────

def to_ui_action(intent: dict[str, Any]) -> dict[str, Any]:
    """
    Produce the action descriptor for UITool / ui_fallback.

    The UI tool reads this dict key-by-key; MappingProxyType wrapping in
    LayeredPayload is transparent to dict-style access.

    Standard keys
    ─────────────
    action          — verb: "click" | "type" | "scroll" | "drag" | "focus"
    target          — selector string, accessibility label, or element id
    value           — text to type, scroll delta, drag destination, etc.
    coords          — {"x": int, "y": int} for pixel-based fallback
    app             — expected focused application name (from AppContext)
    _intent         — routing metadata
    _profile_hint   — forwarded for logging

    The action verb is inferred from the intent name when not supplied
    explicitly — "click_*" intents default to "click", "type_*" to "type",
    etc.  This avoids hardcoding intent names here; instead the mapping
    is derived from the intent's own naming convention.
    """
    params = _get_parameters(intent)
    intent_str = _intent_name(intent)

    action: dict[str, Any] = {}

    # ── Action verb ───────────────────────────────────────────────────────────
    verb: str | None = params.get("action") or params.get("ui_action")
    if not verb:
        # Infer from intent name prefix
        lower = intent_str.lower()
        if lower.startswith("click"):
            verb = "click"
        elif lower.startswith("type") or lower.startswith("input") or lower.startswith("write"):
            verb = "type"
        elif lower.startswith("scroll"):
            verb = "scroll"
        elif lower.startswith("drag"):
            verb = "drag"
        elif lower.startswith("focus") or lower.startswith("switch"):
            verb = "focus"
        elif lower.startswith("open") or lower.startswith("launch"):
            verb = "focus"   # UI "open" means bring into focus
        else:
            verb = "click"   # safe default for unknown UI intents

    action["action"] = verb

    # ── Target selector / element ─────────────────────────────────────────────
    target = (
        params.get("target")
        or params.get("selector")
        or params.get("element")
        or params.get("label")
    )
    if target is not None:
        action["target"] = str(target)

    # ── Value (text to type, scroll amount, etc.) ─────────────────────────────
    value = params.get("value") or params.get("text") or params.get("input")
    if value is not None:
        action["value"] = str(value)

    # ── Pixel coordinate fallback (used by vision_model / screen_reader) ──────
    coords = params.get("coords") or params.get("coordinates")
    if isinstance(coords, dict) and "x" in coords and "y" in coords:
        # deep_freeze in LayeredPayload will freeze the nested dict
        action["coords"] = {"x": int(coords["x"]), "y": int(coords["y"])}

    # ── App context (used by UIReadinessGuard to validate focus) ─────────────
    app = params.get("app") or params.get("application") or params.get("window")
    if app is not None:
        action["app"] = str(app)

    # ── Remaining parameters passed through for UI tool extensions ────────────
    skip = {"action", "ui_action", "target", "selector", "element", "label",
            "value", "text", "input", "coords", "coordinates", "app",
            "application", "window"}
    for k, v in params.items():
        if k not in skip:
            action[k] = v

    # ── Routing metadata ──────────────────────────────────────────────────────
    action["_intent"]       = intent_str
    action["_profile_hint"] = intent.get("profile_hint")

    return action