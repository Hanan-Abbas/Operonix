"""
tools/tool_validator.py
────────────────────────
Unified Validator — merges the old ToolValidator (regex-based safety checks)
with the capability-level validation rules from capabilities/validation_rules.py.

DROP-IN REPLACEMENT for both:
    from tools.tool_validator import tool_validator
    from capabilities.validation_rules import INTENT_VALIDATION   ← still exported here

Architecture
────────────
Validation runs in two layers, in order:

  Layer 1 — Safety Guard (was ToolValidator)
      Blocks universally dangerous patterns (rm -rf /, forbidden paths, etc.)
      Rules are loaded from settings.SAFETY_RULES_FILE (JSON) at startup and
      can be hot-reloaded at runtime via the EventBus without a restart.

  Layer 2 — Intent Semantic Rules (was validation_rules.py / INTENT_VALIDATION)
      Per-intent rules delegated to the capability helpers
      (safe_path_check, validate_text, validate_url, etc.).
      Rules are registered dynamically; new *_ops modules auto-attach.

Self-evolving hook
──────────────────
The learning system can subscribe to "validation_rule_added" events and
call tool_validator.add_safety_rule() at runtime to tighten rules based
on observed failures.
"""
from __future__ import annotations

import json
import logging
import re
from pathlib import Path
from typing import Any, Callable, Coroutine, Optional, Tuple

from core.event_bus import bus

logger = logging.getLogger("ToolValidator")

# Type alias for a validation rule coroutine
RuleFn = Callable[..., Coroutine[Any, Any, Tuple[bool, Optional[str]]]]


# ══════════════════════════════════════════════════════════════════════════ #
#  Layer 1 — Safety Guard                                                    #
# ══════════════════════════════════════════════════════════════════════════ #

class SafetyGuard:
    """
    Loads forbidden command / path patterns from a JSON file so they can
    be updated without touching source code.

    Expected JSON shape (settings.SAFETY_RULES_FILE):
    {
        "forbidden_commands": ["rm\\s+-rf\\s+/", "mkfs", ...],
        "forbidden_paths":    ["/etc/shadow", "C:\\\\Windows\\\\System32", ...]
    }

    Falls back to a minimal built-in set if the file is absent.
    """

    _BUILTIN_FORBIDDEN_COMMANDS = [
        r"rm\s+-rf\s+/",
        r"rm\s+-rf\s+~",
        r"mkfs",
        r"shutdown\s",
        r"format\s+.:",
        r"dd\s+if=.+of=/dev/",
    ]
    _BUILTIN_FORBIDDEN_PATHS = [
        "/etc/shadow",
        "/etc/passwd",
        r"C:\Windows\System32",
        "/boot",
    ]

    def __init__(self) -> None:
        self._cmd_patterns: list[str] = list(self._BUILTIN_FORBIDDEN_COMMANDS)
        self._path_patterns: list[str] = list(self._BUILTIN_FORBIDDEN_PATHS)
        self._load_from_settings()

    def _load_from_settings(self) -> None:
        try:
            from core.config import settings
            rules_file = getattr(settings, "SAFETY_RULES_FILE", None)
            if rules_file and Path(rules_file).exists():
                with open(rules_file, "r", encoding="utf-8") as fh:
                    data = json.load(fh)
                self._cmd_patterns = data.get("forbidden_commands", self._cmd_patterns)
                self._path_patterns = data.get("forbidden_paths", self._path_patterns)
                logger.info(f"🔒 Safety rules loaded from {rules_file}")
        except Exception as exc:
            logger.warning(f"Could not load safety rules file: {exc}. Using built-in defaults.")

    def reload(self) -> None:
        """Hot-reload rules from disk (called by EventBus handler)."""
        self._load_from_settings()
        logger.info("🔄 Safety rules reloaded.")

    def add_command_pattern(self, pattern: str) -> None:
        if pattern not in self._cmd_patterns:
            self._cmd_patterns.append(pattern)

    def add_path_pattern(self, pattern: str) -> None:
        if pattern not in self._path_patterns:
            self._path_patterns.append(pattern)

    def check(self, tool_name: str, action: str, args: dict) -> Tuple[bool, Optional[str]]:
        """Synchronous safety check — runs before async semantic rules."""
        cmd = args.get("command") or args.get("script_path", "")
        if cmd:
            for pattern in self._cmd_patterns:
                if re.search(pattern, str(cmd)):
                    return False, f"DANGEROUS_COMMAND blocked by safety guard: {cmd!r}"

        path = str(args.get("path", "") or args.get("src", "") or args.get("dst", ""))
        if path:
            for forbidden in self._path_patterns:
                if forbidden in path:
                    return False, f"FORBIDDEN_PATH_ACCESS: {path!r}"

        return True, None


# ══════════════════════════════════════════════════════════════════════════ #
#  Layer 2 — Intent Semantic Rules (capability-level)                        #
# ══════════════════════════════════════════════════════════════════════════ #

class SemanticValidator:
    """
    Holds per-intent async rule functions.

    Rules are registered automatically by capabilities/bootstrap.py using
    the same INTENT_VALIDATION dict that used to live in validation_rules.py.
    That dict is still re-exported from this module for backward compatibility.
    """

    def __init__(self) -> None:
        self._rules: dict[str, list[RuleFn]] = {}

    def add_rule(self, intent: str, rule_fn: RuleFn) -> None:
        self._rules.setdefault(intent, []).append(rule_fn)

    def add_rules_bulk(self, mapping: dict[str, list[RuleFn]]) -> None:
        for intent, rules in mapping.items():
            for rule in rules:
                self.add_rule(intent, rule)

    async def validate(
        self, intent: str, action_data: dict, merged_args: dict
    ) -> Tuple[bool, Optional[str]]:
        import asyncio
        for rule in self._rules.get(intent, []):
            try:
                if asyncio.iscoroutinefunction(rule):
                    ok, msg = await rule(action_data, merged_args)
                else:
                    ok, msg = rule(action_data, merged_args)
                if not ok:
                    return False, msg
            except Exception as exc:
                logger.error(f"Rule {rule.__name__} raised: {exc}")
                return False, str(exc)
        return True, None


# ══════════════════════════════════════════════════════════════════════════ #
#  Unified ToolValidator                                                     #
# ══════════════════════════════════════════════════════════════════════════ #

class ToolValidator:
    """
    Single validation entry point for the Executor.

    Usage (unchanged from old API):
        ok, msg = await tool_validator.validate(tool_name, action, args)

    Extended API:
        tool_validator.add_safety_rule(pattern, kind="command"|"path")
        tool_validator.add_intent_rule(intent, rule_fn)
    """

    def __init__(self) -> None:
        self.safety = SafetyGuard()
        self.semantic = SemanticValidator()
        self._wire_eventbus()

    def _wire_eventbus(self) -> None:
        """Subscribe to EventBus so the learning system can inject rules."""
        try:
            bus.subscribe("safety_rules_reload", lambda _data: self.safety.reload())
            bus.subscribe(
                "validation_rule_added",
                lambda data: self.semantic.add_rule(
                    data["intent"], data["rule_fn"]
                ) if "intent" in data and "rule_fn" in data else None,
            )
        except Exception:
            pass  # bus may not be running yet at import time

    # ── Public API ─────────────────────────────────────────────────── #

    async def validate(
        self,
        tool_name: str,
        action: str,
        args: dict,
        intent: str = "",
        action_data: Optional[dict] = None,
    ) -> Tuple[bool, Optional[str]]:
        """
        Full two-layer validation.

        Layer 1: synchronous safety guard (always runs).
        Layer 2: async semantic rules for the given intent (if registered).
        """
        # Layer 1
        safe, msg = self.safety.check(tool_name, action, args)
        if not safe:
            logger.warning(f"🛡  Safety guard blocked '{tool_name}/{action}': {msg}")
            return False, msg

        # Layer 2
        if intent:
            merged = {**(args or {}), **((action_data or {}).get("args") or {})}
            ok, sem_msg = await self.semantic.validate(intent, action_data or {}, merged)
            if not ok:
                logger.warning(f"🚫 Semantic validation failed for '{intent}': {sem_msg}")
                return False, sem_msg

        return True, "Safe"

    def add_safety_rule(self, pattern: str, kind: str = "command") -> None:
        """Runtime injection — used by the learning / debugging systems."""
        if kind == "command":
            self.safety.add_command_pattern(pattern)
        else:
            self.safety.add_path_pattern(pattern)
        bus.publish("validation_rule_added", {"kind": kind, "pattern": pattern}, source="tool_validator")
        logger.info(f"🔒 New safety rule added ({kind}): {pattern!r}")

    def add_intent_rule(self, intent: str, rule_fn: RuleFn) -> None:
        self.semantic.add_rule(intent, rule_fn)


# ── Global singleton ──────────────────────────────────────────────────── #
tool_validator = ToolValidator()


# ══════════════════════════════════════════════════════════════════════════ #
#  Backward-compatible INTENT_VALIDATION export                              #
#  (capabilities/bootstrap.py does `from capabilities.validation_rules …`)  #
#  We rebuild it here from the capability helpers so there's one source.     #
# ══════════════════════════════════════════════════════════════════════════ #

def _build_intent_validation() -> dict:
    """
    Lazily constructs the INTENT_VALIDATION mapping from capability helpers.
    Keeps the old structure so capabilities/bootstrap.py continues to work.
    """
    try:
        from capabilities.file_ops import safe_path_check
        from capabilities.text_ops import validate_text
        from capabilities.web_ops import validate_url
        from capabilities.ui_ops import validate_coordinates
        from capabilities.command_ops import validate_command

        async def rule_safe_path(action_data, merged):
            return await safe_path_check({}, merged)

        async def rule_validate_text(action_data, merged):
            return await validate_text(merged)

        async def rule_validate_url(action_data, merged):
            return await validate_url(merged)

        async def rule_validate_coordinates(action_data, merged):
            return await validate_coordinates(merged)

        async def rule_validate_command(action_data, merged):
            return await validate_command(merged)

        async def rule_screenshot(action_data, merged):
            if merged.get("path"):
                return True, None
            if merged.get("url"):
                return await validate_url(merged)
            return False, "screenshot requires path or url"

        _PATH_INTENTS = [
            "write_file", "append_file", "read_file", "delete_file",
            "move_file", "list_dir", "create_dir", "delete_dir",
        ]

        mapping: dict = {i: [rule_safe_path] for i in _PATH_INTENTS}
        mapping.update({
            "run_command":    [rule_validate_command],
            "execute_script": [rule_validate_command],
            "git_op":         [rule_validate_command],
            "install_package":[rule_validate_command],
            "check_status":   [rule_validate_command],
            "open_url":       [rule_validate_url],
            "click_link":     [rule_validate_url],
            "fill_form":      [rule_validate_url],
            "submit_form":    [rule_validate_url],
            "extract_text":   [rule_validate_url],
            "screenshot":     [rule_screenshot],
            "click":          [rule_validate_coordinates],
            "double_click":   [rule_validate_coordinates],
            "move_cursor":    [rule_validate_coordinates],
            "generate_text":  [rule_validate_text],
            "summarize_text": [rule_validate_text],
            "translate_text": [rule_validate_text],
            "correct_grammar":[rule_validate_text],
            "code_generate":  [rule_validate_text],
            "code_format":    [rule_validate_text],
            "code_analyze":   [rule_validate_text],
            "type_text":      [rule_validate_text],
        })
        return mapping
    except ImportError:
        return {}


# Exported for capabilities/bootstrap.py backward compat
INTENT_VALIDATION: dict = _build_intent_validation()

# Auto-register all built-in intent rules into the semantic validator
tool_validator.semantic.add_rules_bulk(INTENT_VALIDATION)