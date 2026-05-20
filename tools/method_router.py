"""
tools/method_router.py
───────────────────────
Single authoritative routing layer for the Operonix agent.

This module is the ONLY place where method-selection decisions are made.
No other module (orchestrator, executor, intent_parser, tool_selector)
may contain routing logic.

Routing priority (enforced in MethodRouter.select())
─────────────────────────────────────────────────────
  1. PLUGIN  — a registered, trusted plugin scores >= PLUGIN_CONFIDENCE_THRESHOLD
  2. API     — intent maps to a known APITool endpoint (O(1) lookup, no probe)
  3. SHELL   — no plugin/API match; risk score permits shell execution
  4. UI      — all upper methods return zero confidence; static compatibility
               check passes; JIT focus validation is deferred to the executor

Observability (Phase 3)
───────────────────────
Every call to select() publishes a "routing_decision" event on the bus so
api/routes/logs.py and dashboard/components/live_logs.js can surface the
full decision chain without polling.

Integration points
──────────────────
  core/orchestrator.py   → calls router.select(intent) after intent parsing
  executor/executor.py   → consumes MethodDecision; never makes routing calls
  plugins/registry.py    → queried for trusted plugin entries
  tools/tool_registry.py → queried for api_tool / shell_tool / ui_tool entries
  safety/risk_rules.py   → get_command_risk() gates shell selection
  context/app_classifier.py → classifier.classify() for UI compatibility check
  brain/intent_matcher.py   → match_intent_local() for plugin capability scoring
"""

from __future__ import annotations

import logging
import time
from types import MappingProxyType
from typing import Any

from core.config import settings
from core.event_bus import bus
from brain.intent_matcher import match_intent_local
from context.app_classifier import classifier
from plugins.registry import plugin_registry, PluginEntry
from safety.risk_rules import RiskLevel, get_command_risk
from tools.payload_serializers import (
    to_api_body,
    to_plugin_kwargs,
    to_shell_argv,
    to_ui_action,
)
from tools.routing_decision import (
    FailureClass,
    LayeredPayload,
    MethodDecision,
    MethodType,
    deep_freeze,
)

logger = logging.getLogger("MethodRouter")


# ─────────────────────────────────────────────────────────────────────────────
# Configuration keys read from settings (no hardcoding)
# ─────────────────────────────────────────────────────────────────────────────
#
# settings.PLUGIN_EVOLVE_THRESHOLD  (float, default 0.75)
#     Minimum confidence score from match_intent_local() for a plugin
#     to be selected.
#
# settings.INTENT_MATCH_MIN_CONFIDENCE  (float, default 0.35)
#     Floor below which even the best plugin match is rejected.
#
# settings.SAFE_MODE  (bool, default True)
#     When True, shell commands that score RiskLevel.HIGH are rejected
#     and the router falls through to UI.
#
# settings.MAX_RETRY_ATTEMPTS  (int, default 3)
#     Used by the executor for ENV_TRANSIENT retries; stored here for
#     documentation completeness — not consumed by the router itself.
#
# No values are hardcoded in this module.  Every threshold, flag, and
# timeout is read from the settings singleton at call time so they can
# be changed in config or .env without touching router code.


class MethodRouter:
    """
    Central routing authority.

    Public interface
    ────────────────
    router.select(intent) -> MethodDecision

    The returned MethodDecision is immutable and contains:
      • The chosen method
      • Pre-serialized payloads for every execution layer (Gap 2)
      • The ordered fallback chain (Optimization A: tuple, not list)
      • A UI context snapshot when UI is in the chain (Gap 3 / Opt B)
      • The ordered log of rejected methods with reasons (Phase 3)

    Thread safety
    ─────────────
    select() is stateless — all configuration is read from the settings
    singleton and from live registries.  It is safe to call from
    multiple threads or coroutines concurrently.
    """

    # ── Public entry point ────────────────────────────────────────────────────

    def select(self, intent: dict[str, Any]) -> MethodDecision:
        """
        Evaluate the intent against all four execution layers in priority
        order and return a fully-populated, immutable MethodDecision.

        Args:
            intent: The ParsedIntent dict from IntentParser._parse_async():
                    {
                        "intent"      : str | None,
                        "confidence"  : float,
                        "parameters"  : dict,
                        "profile_hint": str | None,
                    }

        Returns:
            MethodDecision — never None.  If all four layers are
            incompatible, a UI decision with confidence=0.0 is returned
            so the executor has a consistent type to work with.
        """
        t_start    = time.monotonic()
        intent_str = str(intent.get("intent") or "")
        rejected   : list[tuple[MethodType, str]] = []

        # ── Build LayeredPayload once (Gap 2) ─────────────────────────────────
        # All four slots are serialized now, before any execution attempt.
        # The executor reads the slot for its current method; on fallback it
        # reads the next slot.  No translation logic exists in the executor.
        payload = self._build_payload(intent)

        # ── Capture UI state snapshot (Gap 3 / Optimization B) ───────────────
        # The snapshot is taken here, at routing time T, so the JIT validator
        # in the executor can compare against a known-good baseline.
        # The executor MUST re-query live state immediately before invocation —
        # this snapshot is only used to confirm that the intent is compatible
        # with UI, not to substitute for the live check.
        ui_snapshot, expected_app = self._capture_ui_snapshot(intent)

        # ── Layer 1: PLUGIN ───────────────────────────────────────────────────
        plugin_result = self._evaluate_plugin(intent_str, intent)
        if plugin_result is not None:
            chosen_method, confidence, fallback = plugin_result
            decision = self._build_decision(
                method=chosen_method,
                confidence=confidence,
                fallback_chain=fallback,
                payload=payload,
                expected_app=expected_app,
                expected_ui_state=ui_snapshot,
                rejected=tuple(rejected),
            )
            self._emit_routing_event(intent_str, decision, t_start)
            return decision

        rejected.append((
            MethodType.PLUGIN,
            self._plugin_rejection_reason(intent_str),
        ))

        # ── Layer 2: API ──────────────────────────────────────────────────────
        api_result = self._evaluate_api(intent_str)
        if api_result is not None:
            chosen_method, confidence, fallback = api_result
            decision = self._build_decision(
                method=chosen_method,
                confidence=confidence,
                fallback_chain=fallback,
                payload=payload,
                expected_app=expected_app,
                expected_ui_state=ui_snapshot,
                rejected=tuple(rejected),
            )
            self._emit_routing_event(intent_str, decision, t_start)
            return decision

        rejected.append((
            MethodType.API,
            f"intent '{intent_str}' not in APITool.supported_intents",
        ))

        # ── Layer 3: SHELL ────────────────────────────────────────────────────
        shell_result = self._evaluate_shell(intent, payload)
        if shell_result is not None:
            chosen_method, confidence, fallback = shell_result
            decision = self._build_decision(
                method=chosen_method,
                confidence=confidence,
                fallback_chain=fallback,
                payload=payload,
                expected_app=expected_app,
                expected_ui_state=ui_snapshot,
                rejected=tuple(rejected),
            )
            self._emit_routing_event(intent_str, decision, t_start)
            return decision

        rejected.append((
            MethodType.SHELL,
            self._shell_rejection_reason(intent, payload),
        ))

        # ── Layer 4: UI (last resort) ─────────────────────────────────────────
        ui_result = self._evaluate_ui(intent_str, expected_app)
        if ui_result is not None:
            chosen_method, confidence, fallback = ui_result
            decision = self._build_decision(
                method=chosen_method,
                confidence=confidence,
                fallback_chain=fallback,
                payload=payload,
                expected_app=expected_app,
                expected_ui_state=ui_snapshot,
                rejected=tuple(rejected),
            )
            self._emit_routing_event(intent_str, decision, t_start)
            return decision

        rejected.append((
            MethodType.UI,
            self._ui_rejection_reason(expected_app),
        ))

        # ── No method viable — return a zero-confidence UI decision ───────────
        # The executor will receive this, find confidence=0.0, and surface a
        # clean "no viable execution path" error rather than a routing crash.
        logger.warning(
            "No viable execution method for intent '%s'. "
            "Returning zero-confidence UI decision.",
            intent_str,
        )
        fallback_empty: tuple[MethodType, ...] = ()
        decision = self._build_decision(
            method=MethodType.UI,
            confidence=0.0,
            fallback_chain=fallback_empty,
            payload=payload,
            expected_app=expected_app,
            expected_ui_state=ui_snapshot,
            rejected=tuple(rejected),
        )
        self._emit_routing_event(intent_str, decision, t_start)
        return decision

    # ─────────────────────────────────────────────────────────────────────────
    # Layer evaluators
    # ─────────────────────────────────────────────────────────────────────────

    def _evaluate_plugin(
        self,
        intent_str: str,
        intent: dict[str, Any],
    ) -> tuple[MethodType, float, tuple[MethodType, ...]] | None:
        """
        Score every trusted plugin against the intent using match_intent_local().

        Returns (MethodType.PLUGIN, confidence, fallback_chain) if the
        highest-scoring plugin exceeds PLUGIN_EVOLVE_THRESHOLD, else None.

        Scoring
        ───────
        match_intent_local() (from brain/intent_matcher.py) returns a
        (matched_intent, score) pair where score is 0.0-1.0.  We run it
        against each plugin's declared capabilities list and take the best
        score across all trusted plugins.

        Threshold
        ─────────
        Read from settings.PLUGIN_EVOLVE_THRESHOLD (default 0.75).  Below
        this threshold, vague intent matches are rejected so the router
        falls through to the API layer rather than invoking the wrong plugin.
        A secondary floor of settings.INTENT_MATCH_MIN_CONFIDENCE (0.35)
        guards against near-zero scores that SequenceMatcher may inflate.
        """
        plugin_threshold: float = float(
            getattr(settings, "PLUGIN_EVOLVE_THRESHOLD", 0.75)
        )
        min_confidence: float = float(
            getattr(settings, "INTENT_MATCH_MIN_CONFIDENCE", 0.35)
        )

        if not intent_str:
            return None

        trusted_entries: list[PluginEntry] = plugin_registry.list_trusted()
        if not trusted_entries:
            return None

        best_score : float            = 0.0
        best_entry : PluginEntry | None = None

        for entry in trusted_entries:
            capabilities: list[str] = list(getattr(entry.manifest, "capabilities", []))
            if not capabilities:
                # Fall back to matching against the plugin's primary intent field
                primary = getattr(entry.manifest, "intent", "")
                capabilities = [primary] if primary else []

            if not capabilities:
                continue

            _matched, score = match_intent_local(
                candidate_text=intent_str,
                allowed_intents=capabilities,
                threshold=min_confidence,
            )
            if score > best_score:
                best_score = score
                best_entry = entry

        if best_entry is None or best_score < plugin_threshold:
            return None

        logger.info(
            "PLUGIN selected: '%s' (score=%.3f, threshold=%.2f)",
            best_entry.name,
            best_score,
            plugin_threshold,
        )
        fallback: tuple[MethodType, ...] = (
            MethodType.API,
            MethodType.SHELL,
            MethodType.UI,
        )
        return MethodType.PLUGIN, best_score, fallback

    def _plugin_rejection_reason(self, intent_str: str) -> str:
        """Produce a human-readable reason string for the rejected log."""
        plugin_threshold: float = float(
            getattr(settings, "PLUGIN_EVOLVE_THRESHOLD", 0.75)
        )
        trusted = plugin_registry.list_trusted()
        if not trusted:
            return "no trusted plugins registered"
        return (
            f"best plugin confidence below threshold {plugin_threshold:.2f} "
            f"for intent '{intent_str}'"
        )

    def _evaluate_api(
        self,
        intent_str: str,
    ) -> tuple[MethodType, float, tuple[MethodType, ...]] | None:
        """
        Check whether the intent maps to a known APITool endpoint.

        APITool.can_handle() does a set membership check against
        APITool.supported_intents — O(1), no network probe.  This is
        exactly the "static route map" the plan specifies.

        The tool_registry may hold multiple api_tool registrations;
        we check all of them and take the first match.
        """
        if not intent_str:
            return None

        try:
            from tools.tool_registry import tool_registry
            api_entries = [
                e for e in tool_registry._entries.values()
                if e.tool_type == "api_tool"
            ]
        except Exception as exc:
            logger.warning("Could not query tool_registry for API tools: %s", exc)
            api_entries = []

        # Also check the module-level singleton directly as a safety net
        try:
            from tools.api_tool import api_tool as _direct_api
            if _direct_api.can_handle(intent_str):
                logger.info("API selected: api_tool.supported_intents match '%s'", intent_str)
                fallback: tuple[MethodType, ...] = (MethodType.SHELL, MethodType.UI)
                return MethodType.API, 1.0, fallback
        except Exception:
            pass

        for entry in api_entries:
            tool = entry.instance
            can = getattr(tool, "can_handle", None)
            if callable(can) and can(intent_str):
                logger.info(
                    "API selected: tool '%s' handles intent '%s'",
                    entry.name, intent_str,
                )
                fallback = (MethodType.SHELL, MethodType.UI)
                return MethodType.API, 1.0, fallback

        return None

    def _evaluate_shell(
        self,
        intent: dict[str, Any],
        payload: LayeredPayload,
    ) -> tuple[MethodType, float, tuple[MethodType, ...]] | None:
        """
        Gate shell selection through safety/risk_rules.get_command_risk().

        The command string is reconstructed from the pre-serialized
        shell_argv in the LayeredPayload so we use exactly the same
        token list the executor would run — no double-serialization.

        Risk gates
        ──────────
        SAFE / LOW   → select shell
        HIGH         → reject when settings.SAFE_MODE is True (default);
                       select when SAFE_MODE is False (dev/testing)
        FORBIDDEN    → always reject regardless of SAFE_MODE

        profile_hint is forwarded to get_command_risk() so the risk
        engine can apply bridge-safe and lab-aware rules correctly
        (see risk_rules.py BUG 2 fix).
        """
        safe_mode: bool = bool(getattr(settings, "SAFE_MODE", True))
        profile_hint: str | None = intent.get("profile_hint")

        if payload.shell_argv is None or len(payload.shell_argv) == 0:
            return None

        # Reconstruct the command string from the frozen argv tuple
        # so risk_rules can apply regex patterns to it.
        command_str = " ".join(payload.shell_argv)

        risk: RiskLevel = get_command_risk(command_str, profile_hint=profile_hint)

        if risk == RiskLevel.FORBIDDEN:
            logger.warning(
                "SHELL rejected: FORBIDDEN risk for command %r", command_str
            )
            return None

        if risk == RiskLevel.HIGH and safe_mode:
            logger.warning(
                "SHELL rejected: HIGH risk in SAFE_MODE for command %r", command_str
            )
            return None

        # Confidence reflects how permissive the risk assessment was
        confidence_map = {
            RiskLevel.SAFE : 1.0,
            RiskLevel.LOW  : 0.85,
            RiskLevel.HIGH : 0.60,   # only reachable when SAFE_MODE is False
        }
        confidence = confidence_map.get(risk, 0.7)

        logger.info(
            "SHELL selected: risk=%s, command=%r, confidence=%.2f",
            risk.name, command_str, confidence,
        )
        fallback: tuple[MethodType, ...] = (MethodType.UI,)
        return MethodType.SHELL, confidence, fallback

    def _shell_rejection_reason(
        self,
        intent: dict[str, Any],
        payload: LayeredPayload,
    ) -> str:
        safe_mode = bool(getattr(settings, "SAFE_MODE", True))
        if payload.shell_argv is None or len(payload.shell_argv) == 0:
            return "no shell_argv produced by serializer"
        cmd = " ".join(payload.shell_argv)
        profile_hint = intent.get("profile_hint")
        risk = get_command_risk(cmd, profile_hint=profile_hint)
        if risk == RiskLevel.FORBIDDEN:
            return f"command risk is FORBIDDEN: {cmd!r}"
        if risk == RiskLevel.HIGH and safe_mode:
            return f"command risk is HIGH and SAFE_MODE=True: {cmd!r}"
        return f"shell not viable for command {cmd!r}"

    def _evaluate_ui(
        self,
        intent_str: str,
        expected_app: str | None,
    ) -> tuple[MethodType, float, tuple[MethodType, ...]] | None:
        """
        Perform the STATIC compatibility check for UI selection.

        This is checkpoint 1 of the two-checkpoint model (Gap 3):
          - Static check (here, at routing time): is this intent class
            ever compatible with UI automation?
          - JIT check (in executor, immediately before invocation):
            is the correct app focused right now?

        An intent is UI-compatible if:
          a) expected_app is not None (we know which app to target), OR
          b) the intent name suggests a GUI interaction by prefix

        UI is NEVER selected speculatively.  If no app is focused and
        the intent has no UI prefix, it is rejected and the router
        returns a zero-confidence UI decision from select().
        """
        # Intent prefixes that imply a UI interaction
        ui_prefixes = (
            "click", "type", "scroll", "drag", "focus",
            "open", "launch", "close", "minimize", "maximize",
            "select", "input", "press", "tap", "hover",
        )
        intent_lower = intent_str.lower()
        has_ui_prefix = any(intent_lower.startswith(p) for p in ui_prefixes)

        if expected_app is None and not has_ui_prefix:
            logger.info(
                "UI rejected: no focused app and intent '%s' has no UI prefix",
                intent_str,
            )
            return None

        confidence = 0.5 if expected_app else 0.3
        logger.info(
            "UI selected: expected_app=%r, intent=%r, confidence=%.2f",
            expected_app, intent_str, confidence,
        )
        fallback: tuple[MethodType, ...] = ()  # UI is the last resort
        return MethodType.UI, confidence, fallback

    def _ui_rejection_reason(self, expected_app: str | None) -> str:
        if expected_app is None:
            return "no focused app detected and intent has no UI prefix"
        return f"UI compatibility check failed for app '{expected_app}'"

    # ─────────────────────────────────────────────────────────────────────────
    # Payload and snapshot builders
    # ─────────────────────────────────────────────────────────────────────────

    def _build_payload(self, intent: dict[str, Any]) -> LayeredPayload:
        """
        Serialize all four execution-layer payloads in one pass (Gap 2).

        Each serializer is called with the full intent dict so it has
        access to parameters, profile_hint, and the intent name.
        LayeredPayload.__post_init__ deep-freezes every container.
        """
        try:
            p_kwargs = to_plugin_kwargs(intent)
        except Exception as exc:
            logger.warning("to_plugin_kwargs failed: %s — using empty dict", exc)
            p_kwargs = {}

        try:
            a_body = to_api_body(intent)
        except Exception as exc:
            logger.warning("to_api_body failed: %s — using empty dict", exc)
            a_body = {}

        try:
            s_argv = to_shell_argv(intent)
        except Exception as exc:
            logger.warning("to_shell_argv failed: %s — using empty list", exc)
            s_argv = []

        try:
            u_action = to_ui_action(intent)
        except Exception as exc:
            logger.warning("to_ui_action failed: %s — using empty dict", exc)
            u_action = {}

        return LayeredPayload(
            plugin_kwargs=p_kwargs or None,
            api_body=a_body or None,
            shell_argv=s_argv if s_argv else None,
            ui_action=u_action or None,
        )

    def _capture_ui_snapshot(
        self,
        intent: dict[str, Any],
    ) -> tuple[MappingProxyType | None, str | None]:
        """
        Capture a lightweight UI state snapshot at routing time T (Gap 3).

        This is NOT a live accessibility tree query — that happens in the
        JIT UIReadinessGuard inside the executor.  This snapshot captures:
          • The currently focused app (from AppClassifier via focus_tracker)
          • The expected UI state from the intent's ui_action parameters

        The executor's JIT validator uses this snapshot only for the static
        compatibility check, not as a substitute for the live AX query.

        Returns (frozen_snapshot, expected_app_name).  Both may be None
        if no app is focused or AppClassifier returns low confidence.
        """
        expected_app: str | None = None
        snapshot_dict: dict[str, Any] = {}

        # Try to get the currently active window title from focus_tracker
        try:
            from context.focus_tracker import focus_tracker
            # focus_tracker stores the last known title from its async loop;
            # reading it synchronously is safe (it is a plain string attribute).
            current_title: str = getattr(
                focus_tracker, "last_known_title", ""
            ) or ""

            if current_title:
                app_ctx = classifier.classify(current_title)
                # Only trust high or medium confidence classifications
                if app_ctx.confidence in ("high", "medium"):
                    expected_app = app_ctx.app_name
                    snapshot_dict["app_name"]  = app_ctx.app_name
                    snapshot_dict["category"]  = app_ctx.category
                    snapshot_dict["title"]     = current_title
                    if app_ctx.sub_context:
                        snapshot_dict["sub_context"] = app_ctx.sub_context
        except Exception as exc:
            logger.debug("Could not capture UI snapshot from focus_tracker: %s", exc)

        # Also check if the intent's parameters explicitly name a target app
        params = intent.get("parameters") or {}
        app_param = (
            params.get("app")
            or params.get("application")
            or params.get("window")
        )
        if app_param and not expected_app:
            expected_app = str(app_param)
            snapshot_dict["app_name"] = expected_app

        if not snapshot_dict:
            return None, expected_app

        return deep_freeze(snapshot_dict), expected_app

    # ─────────────────────────────────────────────────────────────────────────
    # Decision factory and event emission
    # ─────────────────────────────────────────────────────────────────────────

    @staticmethod
    def _build_decision(
        method           : MethodType,
        confidence       : float,
        fallback_chain   : tuple[MethodType, ...],
        payload          : LayeredPayload,
        expected_app     : str | None,
        expected_ui_state: MappingProxyType | None,
        rejected         : tuple[tuple[MethodType, str], ...],
    ) -> MethodDecision:
        return MethodDecision(
            method=method,
            confidence=confidence,
            fallback_chain=fallback_chain,
            payload=payload,
            expected_app=expected_app,
            expected_ui_state=expected_ui_state,
            rejected=rejected,
        )

    @staticmethod
    def _emit_routing_event(
        intent_str : str,
        decision   : MethodDecision,
        t_start    : float,
    ) -> None:
        """
        Publish a structured "routing_decision" event (Phase 3 observability).

        The event is consumed by:
          • api/routes/logs.py         → structured log entry
          • core/event_bus.py          → fires to all subscribers
          • dashboard/live_logs.js     → surfaced in the dashboard

        bus.publish() is used (not bus.emit()) because this is called
        from synchronous context.
        """
        duration_ms = round((time.monotonic() - t_start) * 1000, 2)
        bus.publish(
            "routing_decision",
            {
                "intent"        : intent_str,
                "chosen_method" : decision.method.value,
                "confidence"    : round(decision.confidence, 4),
                "fallback_chain": [m.value for m in decision.fallback_chain],
                "rejected"      : [
                    {"method": m.value, "reason": r}
                    for m, r in decision.rejected
                ],
                "duration_ms"   : duration_ms,
            },
            source="method_router",
        )
        logger.info(
            "routing_decision: intent='%s' method=%s confidence=%.3f "
            "fallback=%s rejected=%d took=%.1fms",
            intent_str,
            decision.method.value,
            decision.confidence,
            [m.value for m in decision.fallback_chain],
            len(decision.rejected),
            duration_ms,
        )


# ─────────────────────────────────────────────────────────────────────────────
# Module-level singleton
# ─────────────────────────────────────────────────────────────────────────────

router = MethodRouter()