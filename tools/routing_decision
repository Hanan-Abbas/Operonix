"""
tools/routing_decision.py
─────────────────────────
Canonical immutable data contracts for the method-routing layer.

All other modules import from here — nothing downstream defines its own
copies of these types.

Design guarantees (Optimization A)
────────────────────────────────────
• Every dataclass uses frozen=True, which blocks field rebinding.
• Every mutable container (dict, list) is coerced to an immutable
  equivalent inside __post_init__ via deep_freeze(), so a failing
  fallback step cannot silently corrupt the payload the next step
  would read.
• fallback_chain is a tuple[MethodType, ...], not a list, so no
  handler can pop() items off the chain mid-execution.
• expected_ui_state is stored as MappingProxyType so the snapshot
  taken at routing time cannot be altered before the JIT validator
  reads it (Optimization B / Gap 3).
"""

from __future__ import annotations

import dataclasses
from dataclasses import dataclass, field
from enum import Enum
from types import MappingProxyType
from typing import Any


# ─────────────────────────────────────────────────────────────────────────────
# Deep-freeze utility
# ─────────────────────────────────────────────────────────────────────────────

def deep_freeze(obj: Any) -> Any:
    """
    Recursively convert:
        dict  → MappingProxyType
        list  → tuple

    Scalars (str, int, float, bool, None, Enum members, …) pass through
    unchanged.  Nested structures are fully traversed so that no value
    at any depth remains mutable after construction.
    """
    if isinstance(obj, dict):
        return MappingProxyType({k: deep_freeze(v) for k, v in obj.items()})
    if isinstance(obj, list):
        return tuple(deep_freeze(item) for item in obj)
    return obj


# ─────────────────────────────────────────────────────────────────────────────
# Enumerations
# ─────────────────────────────────────────────────────────────────────────────

class MethodType(str, Enum):
    """
    The four execution paths, listed in strict descending priority order.

    The ordering here is documentation only — priority enforcement lives
    entirely in MethodRouter.select().  No code should branch on the
    integer value of these members.
    """
    PLUGIN = "plugin"   # Priority 1 — sandboxed, reversible
    API    = "api"      # Priority 2 — structured, deterministic
    SHELL  = "shell"    # Priority 3 — risk-gated shell execution
    UI     = "ui"       # Priority 4 — last resort, JIT-validated


class FailureClass(str, Enum):
    """
    Precise classification of why an execution attempt failed.

    The executor writes one of these into MethodDecision.failure_class
    before publishing to the event bus.  Only ROUTING_MISMATCH is fed
    to learning/learner.py — all other classes must not alter routing
    weights (Gap 1 fix).

    Decision table
    ──────────────
    ROUTING_MISMATCH : feed learner (down-weight) | descend fallback | no retry
    ENV_TRANSIENT    : never feed learner          | no fallback      | retry x N
    ENV_PERMANENT    : mark method unavailable     | descend fallback | no retry
    EXECUTION_LOGIC  : never feed learner          | no fallback      | surface bug
    """
    ROUTING_MISMATCH = "routing_mismatch"   # wrong method chosen for this intent
    ENV_TRANSIENT    = "env_transient"      # network drop, locked file, missing dep
    ENV_PERMANENT    = "env_permanent"      # auth revoked, endpoint removed
    EXECUTION_LOGIC  = "execution_logic"    # method correct, implementation bug


# ─────────────────────────────────────────────────────────────────────────────
# Payload — one slot per execution layer, all filled at parse time (Gap 2)
# ─────────────────────────────────────────────────────────────────────────────

@dataclass(frozen=True)
class LayeredPayload:
    """
    Pre-serialized arguments for every execution layer.

    All four slots are populated by payload_serializers at routing time,
    before any execution attempt begins.  The executor reads the slot
    matching the chosen method; on fallback it reads the next slot.
    No translation logic exists in the executor.

    Immutability contract (Optimization A)
    ───────────────────────────────────────
    frozen=True blocks field rebinding.  __post_init__ coerces every
    mutable container to its immutable equivalent so that internal
    mutation (e.g. dict["key"] = ...) is also blocked at every depth.

    Executor compatibility
    ──────────────────────
    • plugin_kwargs  — passed as **kwargs to BasePlugin.run(context, args)
    • api_body       — serialized to JSON for aiohttp; MappingProxyType
                       is accepted by json.dumps() transparently
    • shell_argv     — passed directly to subprocess.run(); tuple is
                       accepted natively without conversion
    • ui_action      — consumed key-by-key by the UI tool; MappingProxyType
                       is transparent to dict-style reads
    """
    plugin_kwargs : MappingProxyType | None  # typed kwargs for BasePlugin
    api_body      : MappingProxyType | None  # JSON-serializable body for APITool
    shell_argv    : tuple[str, ...] | None   # tokenized argv for ShellTool
    ui_action     : MappingProxyType | None  # action descriptor for UITool

    def __post_init__(self) -> None:
        # Coerce at construction time so callers may pass plain dicts/lists.
        # object.__setattr__ is required because frozen=True blocks normal
        # attribute assignment even inside __post_init__.
        if self.plugin_kwargs is not None and not isinstance(self.plugin_kwargs, MappingProxyType):
            object.__setattr__(self, "plugin_kwargs", deep_freeze(dict(self.plugin_kwargs)))

        if self.api_body is not None and not isinstance(self.api_body, MappingProxyType):
            object.__setattr__(self, "api_body", deep_freeze(dict(self.api_body)))

        if self.shell_argv is not None and not isinstance(self.shell_argv, tuple):
            object.__setattr__(self, "shell_argv", tuple(str(t) for t in self.shell_argv))

        if self.ui_action is not None and not isinstance(self.ui_action, MappingProxyType):
            object.__setattr__(self, "ui_action", deep_freeze(dict(self.ui_action)))

    def for_method(self, method: "MethodType") -> "MappingProxyType | tuple | None":
        """Return the pre-serialized payload slot for *method*."""
        return {
            MethodType.PLUGIN : self.plugin_kwargs,
            MethodType.API    : self.api_body,
            MethodType.SHELL  : self.shell_argv,
            MethodType.UI     : self.ui_action,
        }[method]


# ─────────────────────────────────────────────────────────────────────────────
# Decision — the router's complete output
# ─────────────────────────────────────────────────────────────────────────────

@dataclass(frozen=True)
class MethodDecision:
    """
    The complete, immutable output of MethodRouter.select().

    The executor receives exactly one MethodDecision per intent and uses
    it as its sole source of truth for the entire execution lifecycle,
    from the first attempt through every fallback step.

    Fields
    ──────
    method
        The chosen execution path.

    confidence
        Numeric score (0.0-1.0) that caused this method to be selected.
        Used by the learner when failure_class is ROUTING_MISMATCH.

    fallback_chain
        Ordered tuple of methods to try if *method* fails.
        Stored as tuple (not list) so handlers cannot pop() items
        off the chain mid-execution (Optimization A).

    payload
        All four execution-layer payloads, pre-serialized at routing
        time.  Executor calls payload.for_method(next_method) on each
        fallback step — no translation occurs at execution time.

    expected_app
        The app_name (from AppContext) that must be focused when the
        UI layer executes.  Populated only when UI is in the fallback
        chain; None otherwise.

    expected_ui_state
        A frozen snapshot of the accessibility-tree state captured at
        routing time.  The JIT UIReadinessGuard compares a live
        snapshot against this immediately before invocation
        (Gap 3 + Optimization B).  Stored as MappingProxyType.

    rejected
        Ordered record of methods evaluated and skipped, with the
        reason for each.  Emitted in routing_decision event for
        observability (Phase 3).

    failure_class / failure_detail
        Written by the executor on failure before publishing to the
        event bus.  None while the decision is in-flight.
    """
    # ── Routing ───────────────────────────────────────────────────────────────
    method         : MethodType
    confidence     : float
    fallback_chain : tuple  # tuple[MethodType, ...] — Optimization A

    # ── Gap 2 — all layer payloads filled at parse time ───────────────────────
    payload : LayeredPayload

    # ── Gap 3 + Opt B — UI context snapshot for JIT validation ───────────────
    expected_app      : str | None
    expected_ui_state : MappingProxyType | None

    # ── Observability — rejected candidates with reasons ──────────────────────
    # tuple of (MethodType, reason_str) pairs
    rejected : tuple = field(default_factory=tuple)

    # ── Gap 1 — executor writes on failure before event emission ──────────────
    failure_class  : FailureClass | None = None
    failure_detail : str | None          = None

    def __post_init__(self) -> None:
        # Guarantee fallback_chain is always a tuple
        if not isinstance(self.fallback_chain, tuple):
            object.__setattr__(self, "fallback_chain", tuple(self.fallback_chain))
        # Freeze expected_ui_state if a plain dict was supplied
        if (
            self.expected_ui_state is not None
            and not isinstance(self.expected_ui_state, MappingProxyType)
        ):
            object.__setattr__(
                self, "expected_ui_state", deep_freeze(dict(self.expected_ui_state))
            )
        # Guarantee rejected is always a tuple
        if not isinstance(self.rejected, tuple):
            object.__setattr__(self, "rejected", tuple(self.rejected))

    # ── Convenience helpers used by the executor ──────────────────────────────

    def with_failure(
        self,
        failure_class: FailureClass,
        detail: str = "",
    ) -> "MethodDecision":
        """
        Return a new MethodDecision with failure fields set.

        Because the dataclass is frozen, this produces a new instance
        with all other fields unchanged.  The executor replaces its
        local reference before publishing to the event bus.
        """
        return dataclasses.replace(
            self,
            failure_class=failure_class,
            failure_detail=detail,
        )

    def next_method(self) -> "MethodType | None":
        """
        Return the next method from fallback_chain, or None if exhausted.
        The executor calls this after recording a failure.
        """
        return self.fallback_chain[0] if self.fallback_chain else None

    def advance(self) -> "MethodDecision":
        """
        Return a new MethodDecision promoted to the next fallback method.
        Raises IndexError if the chain is already exhausted — callers
        must check next_method() first.
        """
        if not self.fallback_chain:
            raise IndexError("fallback_chain is exhausted; cannot advance.")
        return dataclasses.replace(
            self,
            method=self.fallback_chain[0],
            fallback_chain=self.fallback_chain[1:],
            failure_class=None,
            failure_detail=None,
        )

    def to_log_dict(self) -> dict:
        """
        Serialize to a plain dict for the structured routing log emitted
        by api/routes/logs.py (Phase 3 observability).
        """
        return {
            "method"        : self.method.value,
            "confidence"    : round(self.confidence, 4),
            "fallback_chain": [m.value for m in self.fallback_chain],
            "expected_app"  : self.expected_app,
            "rejected"      : [
                {"method": m.value, "reason": r} for m, r in self.rejected
            ],
            "failure_class" : self.failure_class.value if self.failure_class else None,
            "failure_detail": self.failure_detail,
        }