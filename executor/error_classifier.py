"""
executor/error_classifier.py
─────────────────────────────
Smart error classification with FailureClass tagging (Gap 1 fix).

Changes from original
──────────────────────
The original classifier produced a free-form category string
(permission_denied, not_found, timeout, …) that callers used
inconsistently.  The executor's learning pipeline fed ALL failures to
learner.py, causing ENV_TRANSIENT errors (network drops, locked files)
to corrupt routing weights — the "death spiral" documented in Gap 1.

This revision adds:

1. FailureClass tagging — every classified error is mapped to one of
   the four FailureClass values defined in tools/routing_decision.py.
   The executor uses this to decide:
     ROUTING_MISMATCH → feed learner + descend fallback chain
     ENV_TRANSIENT    → retry same method (backoff ×N), never feed learner
     ENV_PERMANENT    → mark method unavailable + descend fallback chain
     EXECUTION_LOGIC  → surface as bug, no learner feed, no fallback

2. classify_with_failure_class() — new primary method that returns both
   the legacy category string and the FailureClass so callers that only
   need one don't have to change their interface.

3. from_exception() — typed classifier that inspects the exception class
   before falling back to message-based regex, giving the highest
   accuracy for structured exceptions (APITransientError, APIPermanentError,
   asyncio.TimeoutError, PermissionError, etc.).

4. LLM fallback is preserved unchanged.  It is only reached when both
   regex and exception-type checks return no match.

Routing decision table (Gap 1)
────────────────────────────────
ROUTING_MISMATCH → feed learner | descend fallback | no retry
ENV_TRANSIENT    → no learner   | no fallback      | retry ×N with backoff
ENV_PERMANENT    → no learner*  | descend fallback | no retry
                   (* mark method unavailable)
EXECUTION_LOGIC  → no learner   | no fallback      | surface bug
"""

from __future__ import annotations

import asyncio
import json
import logging
import re
from typing import Optional

from brain.llm_client import llm_client
from tools.routing_decision import FailureClass

logger = logging.getLogger("ErrorClassifier")


# ─────────────────────────────────────────────────────────────────────────────
# Category → FailureClass mapping
# ─────────────────────────────────────────────────────────────────────────────
# Every legacy category string maps to exactly one FailureClass.
# The learner, fallback manager, and retry manager consume FailureClass —
# they never branch on the raw category string.

_CATEGORY_TO_FAILURE_CLASS: dict[str, FailureClass] = {
    # Retryable environment errors — never feed the learner
    "timeout"            : FailureClass.ENV_TRANSIENT,
    "network_error"      : FailureClass.ENV_TRANSIENT,
    "resource_exhausted" : FailureClass.ENV_TRANSIENT,

    # Permanent environment errors — method should be marked unavailable
    "permission_denied"  : FailureClass.ENV_PERMANENT,
    "not_found"          : FailureClass.ENV_PERMANENT,
    "auth_error"         : FailureClass.ENV_PERMANENT,

    # Method was wrong for this intent — feed the learner to down-weight
    "routing_mismatch"   : FailureClass.ROUTING_MISMATCH,
    "wrong_method"       : FailureClass.ROUTING_MISMATCH,
    "invalid_input"      : FailureClass.ROUTING_MISMATCH,

    # Bug in the implementation — route was correct, code is broken
    "execution_logic"    : FailureClass.EXECUTION_LOGIC,
    "interface_mismatch" : FailureClass.EXECUTION_LOGIC,
    "type_error"         : FailureClass.EXECUTION_LOGIC,

    # Unknown — treat as transient so we don't permanently penalise a method
    "unknown_error"      : FailureClass.ENV_TRANSIENT,
}


# ─────────────────────────────────────────────────────────────────────────────
# Exception-type → FailureClass direct map
# ─────────────────────────────────────────────────────────────────────────────
# Checked before regex so structured exceptions get the most accurate tag.

def _failure_class_from_exception(exc: BaseException) -> FailureClass | None:
    """
    Map a known exception type directly to a FailureClass without touching
    the message string.  Returns None when the type is unrecognised so the
    caller falls through to regex matching.
    """
    # Lazy import to avoid circular dependency at module load time
    try:
        from tools.api_tool import APITransientError, APIPermanentError
        if isinstance(exc, APITransientError):
            return FailureClass.ENV_TRANSIENT
        if isinstance(exc, APIPermanentError):
            return FailureClass.ENV_PERMANENT
    except ImportError:
        pass

    if isinstance(exc, asyncio.TimeoutError):
        return FailureClass.ENV_TRANSIENT
    if isinstance(exc, (ConnectionError, ConnectionRefusedError,
                        ConnectionResetError, BrokenPipeError)):
        return FailureClass.ENV_TRANSIENT
    if isinstance(exc, PermissionError):
        return FailureClass.ENV_PERMANENT
    if isinstance(exc, FileNotFoundError):
        return FailureClass.ENV_PERMANENT
    if isinstance(exc, (TypeError, AttributeError, NotImplementedError)):
        return FailureClass.EXECUTION_LOGIC
    if isinstance(exc, (ValueError, KeyError)):
        # Could be routing mismatch (wrong args) or logic bug — default to mismatch
        # so the learner can adjust, rather than silently treating it as transient.
        return FailureClass.ROUTING_MISMATCH

    return None   # unknown — fall through to regex


class ErrorClassifier:
    """
    Classify errors with FailureClass tagging for the Gap 1 fix.

    Primary interface
    ─────────────────
    await classify_with_failure_class(error_message) -> (category, FailureClass)
    await from_exception(exc) -> (category, FailureClass)

    Legacy interface (preserved for callers that use the old string API)
    ──────────────────────────────────────────────────────────────────────
    await classify(error_message) -> category_str
    await get_retry_strategy(category) -> dict
    """

    # ── Regex patterns (unchanged from original — no behaviour change) ────────
    FALLBACK_PATTERNS: dict[str, re.Pattern] = {
        "permission_denied": re.compile(
            r"(permission denied|access denied|forbidden|not allowed|"
            r"operation not permitted|user is not in sudoers|"
            r"sudo: .* command not found)",
            re.IGNORECASE,
        ),
        "not_found": re.compile(
            r"(no such file|file not found|does not exist|not found|"
            r"cannot find|cannot open|404|path does not exist)",
            re.IGNORECASE,
        ),
        "timeout": re.compile(
            r"(timeout|timed out|deadline exceeded|took too long|"
            r"connection timeout|read timeout|write timeout)",
            re.IGNORECASE,
        ),
        "network_error": re.compile(
            r"(connection refused|connection reset|network unreachable|"
            r"no route to host|offline|cannot connect|broken pipe|connection lost)",
            re.IGNORECASE,
        ),
        "resource_exhausted": re.compile(
            r"(out of memory|no space left|resource busy|too many open files|"
            r"memory allocation failed|oom|disk full)",
            re.IGNORECASE,
        ),
        "invalid_input": re.compile(
            r"(invalid argument|bad request|syntax error|parse error|"
            r"malformed|invalid option|unrecognized|wrong number of arguments)",
            re.IGNORECASE,
        ),
        "auth_error": re.compile(
            r"(authentication failed|unauthorized|invalid token|"
            r"credentials|api key|401|403)",
            re.IGNORECASE,
        ),
        "interface_mismatch": re.compile(
            r"(has no attribute|unexpected keyword|got an unexpected|"
            r"takes \d+ positional|missing \d+ required|interface mismatch)",
            re.IGNORECASE,
        ),
    }

    def __init__(self) -> None:
        self.logger       = logging.getLogger("ErrorClassifier")
        self.llm_available = True

    # ── New primary interface ─────────────────────────────────────────────────

    async def classify_with_failure_class(
        self,
        error_message: str,
        timeout_seconds: float = 2.0,
    ) -> tuple[str, FailureClass]:
        """
        Classify an error string and return both the legacy category and
        the FailureClass for Gap 1 routing decisions.

        Returns (category_str, FailureClass).
        """
        category = await self.classify(error_message, timeout_seconds)
        failure_class = _CATEGORY_TO_FAILURE_CLASS.get(
            category, FailureClass.ENV_TRANSIENT
        )
        logger.debug(
            "classify_with_failure_class: category='%s' -> FailureClass.%s",
            category, failure_class.name,
        )
        return category, failure_class

    async def from_exception(
        self,
        exc: BaseException,
        timeout_seconds: float = 2.0,
    ) -> tuple[str, FailureClass]:
        """
        Classify a live exception instance.

        Priority:
          1. Exception type map (fastest, most accurate for structured exceptions)
          2. Message regex (for generic exceptions with descriptive messages)
          3. LLM (with timeout guard)
          4. Default: ENV_TRANSIENT (safe default — never corrupts the learner)

        Returns (category_str, FailureClass).
        """
        # 1. Structured exception type check
        direct = _failure_class_from_exception(exc)
        if direct is not None:
            # Reverse-map FailureClass to a canonical category string
            reverse: dict[FailureClass, str] = {
                FailureClass.ENV_TRANSIENT    : "network_error",
                FailureClass.ENV_PERMANENT    : "permission_denied",
                FailureClass.ROUTING_MISMATCH : "routing_mismatch",
                FailureClass.EXECUTION_LOGIC  : "execution_logic",
            }
            category = reverse.get(direct, "unknown_error")
            logger.debug(
                "from_exception: %s → category='%s' FailureClass.%s (type match)",
                type(exc).__name__, category, direct.name,
            )
            return category, direct

        # 2. Fall back to message-based classification
        category, failure_class = await self.classify_with_failure_class(
            str(exc), timeout_seconds
        )
        return category, failure_class

    # ── Legacy interface (unchanged behaviour) ────────────────────────────────

    async def classify(
        self,
        error_message: str,
        timeout_seconds: float = 2.0,
    ) -> str:
        """
        Classify error with priority:
          1. Fast regex patterns
          2. LLM (with timeout)
          3. Default: unknown_error

        Returns category string.
        """
        error_lower = (error_message or "").lower().strip()
        if not error_lower:
            return "unknown_error"

        category = self._classify_by_patterns(error_lower)
        if category != "unknown_error":
            self.logger.debug(
                "Error classified as '%s' (regex pattern match)", category
            )
            return category

        if self.llm_available:
            category = await self._classify_by_llm(error_message, timeout_seconds)
            if category != "unknown_error":
                self.logger.debug("Error classified as '%s' (LLM)", category)
                return category
            else:
                self.logger.warning("LLM returned unknown_error or failed")

        self.logger.warning(
            "Error classification failed: '%s...'", error_message[:100]
        )
        return "unknown_error"

    def _classify_by_patterns(self, error_lower: str) -> str:
        """Fast regex-based error classification."""
        for category, pattern in self.FALLBACK_PATTERNS.items():
            if pattern.search(error_lower):
                return category
        return "unknown_error"

    async def _classify_by_llm(
        self,
        error_message: str,
        timeout_seconds: float,
    ) -> str:
        """Classify using LLM with timeout guard."""
        valid_categories = set(self.FALLBACK_PATTERNS.keys()) | {
            "routing_mismatch", "execution_logic", "unknown_error"
        }
        prompt = (
            f"Classify this error into ONE category:\n"
            f"{chr(10).join(f'- {c}' for c in sorted(valid_categories))}\n\n"
            f'Error: "{error_message}"\n\n'
            f'Return ONLY valid JSON: {{"category": "<category_name>"}}'
        )
        try:
            response = await asyncio.wait_for(
                llm_client.generate(prompt, use_json=True),
                timeout=timeout_seconds,
            )
            if isinstance(response, dict):
                category = response.get("category", "unknown_error")
            else:
                try:
                    data = json.loads(str(response))
                    category = data.get("category", "unknown_error")
                except json.JSONDecodeError:
                    category = "unknown_error"

            if category not in valid_categories:
                self.logger.warning(
                    "LLM returned invalid category '%s'", category
                )
                return "unknown_error"
            return category

        except asyncio.TimeoutError:
            self.logger.warning(
                "LLM classification timed out after %.1fs", timeout_seconds
            )
            return "unknown_error"
        except Exception as exc:
            self.logger.warning("LLM classification failed: %s", exc)
            self.llm_available = False
            return "unknown_error"

    async def get_retry_strategy(self, category: str) -> dict:
        """
        Suggest retry strategy based on error category.

        NOTE: callers should use FailureClass directly for routing decisions.
        This method is preserved for components that display human-readable
        retry guidance (dashboard, logs).
        """
        strategies: dict[str, dict] = {
            "permission_denied": {
                "should_retry": False,
                "failure_class": FailureClass.ENV_PERMANENT.value,
                "reason": "Permission denied — cannot retry without privilege change",
                "suggestion": "Check user privileges or file permissions",
            },
            "auth_error": {
                "should_retry": False,
                "failure_class": FailureClass.ENV_PERMANENT.value,
                "reason": "Authentication failed — credentials invalid or revoked",
                "suggestion": "Renew API key or re-authenticate",
            },
            "not_found": {
                "should_retry": False,
                "failure_class": FailureClass.ENV_PERMANENT.value,
                "reason": "Resource not found — will not appear on retry",
                "suggestion": "Verify the path or resource exists",
            },
            "timeout": {
                "should_retry": True,
                "failure_class": FailureClass.ENV_TRANSIENT.value,
                "reason": "Timeout — may succeed on retry",
                "suggestion": "Retry with increased timeout or reduced payload",
                "backoff_ms": 1000,
            },
            "network_error": {
                "should_retry": True,
                "failure_class": FailureClass.ENV_TRANSIENT.value,
                "reason": "Network issue — may be transient",
                "suggestion": "Check network connectivity and retry",
                "backoff_ms": 2000,
            },
            "resource_exhausted": {
                "should_retry": True,
                "failure_class": FailureClass.ENV_TRANSIENT.value,
                "reason": "Resource temporarily exhausted",
                "suggestion": "Retry after freeing resources",
                "backoff_ms": 5000,
            },
            "invalid_input": {
                "should_retry": False,
                "failure_class": FailureClass.ROUTING_MISMATCH.value,
                "reason": "Invalid input — wrong method for this intent",
                "suggestion": "Router should select a different execution method",
            },
            "routing_mismatch": {
                "should_retry": False,
                "failure_class": FailureClass.ROUTING_MISMATCH.value,
                "reason": "Wrong method selected for this intent",
                "suggestion": "Learner should down-weight this method for this intent pattern",
            },
            "execution_logic": {
                "should_retry": False,
                "failure_class": FailureClass.EXECUTION_LOGIC.value,
                "reason": "Implementation bug — route was correct, code is broken",
                "suggestion": "Surface to debugging/error_listener.py for auto-fix",
            },
            "interface_mismatch": {
                "should_retry": False,
                "failure_class": FailureClass.EXECUTION_LOGIC.value,
                "reason": "Tool interface mismatch — wrong signature",
                "suggestion": "Check tool.run() signature against executor expectations",
            },
            "unknown_error": {
                "should_retry": True,
                "failure_class": FailureClass.ENV_TRANSIENT.value,
                "reason": "Unknown error — treated as transient to avoid learner corruption",
                "suggestion": "Retry with exponential backoff",
                "backoff_ms": 1000,
            },
        }
        return strategies.get(category, strategies["unknown_error"])


# Global singleton
error_classifier = ErrorClassifier()