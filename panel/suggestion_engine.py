"""
panel/suggestion_engine.py

Incremental suggestion engine — as the user types, it calls the intent
parser and ranks execution strategies using the plugin→api→cmd→ui waterfall.

The ranked list is shown in the panel BEFORE the user hits Enter, so they
can see (and override) the planned execution method.

The override signal is published on the EventBus and consumed by
learning/learner.py to improve future rankings.
"""
from __future__ import annotations

import asyncio
import logging
from dataclasses import dataclass, field
from typing import Any, Callable

log = logging.getLogger(__name__)


# ---------------------------------------------------------------------------
# Data model
# ---------------------------------------------------------------------------

@dataclass
class ExecutionStrategy:
    """One candidate execution strategy for a query."""

    method: str          # "plugin" | "api" | "command" | "shell" | "ui"
    label: str           # human-readable label, e.g. "VS Code Plugin — open file"
    description: str     # what will actually happen
    confidence: float    # 0.0 – 1.0
    plugin_name: str | None = None
    capability_id: str | None = None
    metadata: dict[str, Any] = field(default_factory=dict)

    @property
    def method_rank(self) -> int:
        """Lower = preferred in the waterfall."""
        return {"plugin": 0, "api": 1, "command": 2, "shell": 3, "ui": 4}.get(self.method, 99)


@dataclass
class SuggestionResult:
    query: str
    strategies: list[ExecutionStrategy]
    intent: str | None
    app_context: str | None

    @property
    def top(self) -> ExecutionStrategy | None:
        return self.strategies[0] if self.strategies else None


# ---------------------------------------------------------------------------
# Shell confidence heuristic
# ---------------------------------------------------------------------------

_SHELL_PREFIXES: frozenset[str] = frozenset({
    "ls", "cd", "pwd", "cat", "echo", "grep", "find", "sed", "awk",
    "cp", "mv", "rm", "mkdir", "rmdir", "touch", "chmod", "chown",
    "git", "docker", "kubectl", "python", "python3", "pip", "pip3",
    "node", "npm", "yarn", "pnpm", "cargo", "go", "make", "cmake",
    "sudo", "su", "ssh", "scp", "curl", "wget", "tar", "zip", "unzip",
    "ps", "kill", "top", "htop", "df", "du", "free", "env", "export",
    "source", "bash", "sh", "zsh", "fish",
})

_SHELL_INTENTS: frozenset[str] = frozenset({
    "run_command", "execute_script", "file_operation", "git_operation",
    "package_management", "process_management", "system_info",
    "network_request", "build", "test", "deploy",
})


def _shell_confidence(intent: str, intent_data: dict[str, Any]) -> float:
    """
    Heuristic confidence that a query is best served by a shell command.
    Blends a rule-based base score with the intent parser's own confidence.
    """
    query: str = intent_data.get("query", intent_data.get("raw", ""))
    stripped = query.strip()
    intent_conf: float = float(intent_data.get("confidence", 0.5))

    if stripped.startswith("./") or stripped.startswith("/"):
        base = 0.90
    else:
        first_word = stripped.split()[0].lower() if stripped else ""
        if first_word in _SHELL_PREFIXES:
            base = 0.82
        elif intent in _SHELL_INTENTS:
            base = 0.75
        else:
            base = 0.35

    return round(min(max((base * intent_conf) ** 0.5, 0.10), 1.0), 3)


# ---------------------------------------------------------------------------
# Waterfall logic
# ---------------------------------------------------------------------------

_METHOD_LABELS = {
    "plugin":  "Plugin",
    "api":     "API",
    "command": "Command",
    "shell":   "Shell",
    "ui":      "UI Automation",
}


class SuggestionEngine:
    """
    Ranks execution strategies for a partial or complete query.

    Dependencies are injected so the engine works without importing
    any concrete subsystem — keeping panel/ fully decoupled.

    Args:
        intent_parser:   Callable[str] -> dict with at least 'intent' and
                         'confidence' keys (wraps brain/intent_parser.py).
        plugin_registry: Callable[str, str] -> list[dict] — given (app, intent)
                         returns matching plugin manifests.
        capability_registry: Callable[str] -> list[dict] — given intent returns
                              matching capabilities.
        event_bus:       EventBus instance for publishing overrides.
        learned_ranking: Optional callable(app, intent) -> list[str] returning
                         method order learned by learning/learner.py.
    """

    def __init__(
        self,
        intent_parser: Callable[[str], dict[str, Any]],
        plugin_registry: Callable[[str, str], list[dict[str, Any]]],
        capability_registry: Callable[[str], list[dict[str, Any]]],
        event_bus: Any,
        learned_ranking: Callable[[str, str], list[str]] | None = None,
    ) -> None:
        self._parse_intent = intent_parser
        self._plugin_registry = plugin_registry
        self._capability_registry = capability_registry
        self._bus = event_bus
        self._learned_ranking = learned_ranking

        self._current_app: str = "unknown"
        self._debounce_task: asyncio.Task | None = None  # type: ignore[type-arg]

    # ------------------------------------------------------------------
    # Public API
    # ------------------------------------------------------------------

    def set_app_context(self, app_name: str) -> None:
        """Called when app_context_changed fires on the EventBus."""
        self._current_app = app_name
        log.debug("suggestion_engine: app context → %s", app_name)

    async def suggest(self, query: str) -> SuggestionResult:
        """
        Synchronously rank strategies for *query*.
        The panel calls this after the debounce delay.
        """
        if not query.strip():
            return SuggestionResult(query=query, strategies=[], intent=None,
                                    app_context=self._current_app)

        intent_data = await asyncio.to_thread(self._parse_intent, query)
        intent = intent_data.get("intent", "unknown")
        app = self._current_app

        strategies = self._build_waterfall(intent, intent_data, app)
        strategies = self._apply_learned_ranking(strategies, app, intent)

        log.debug(
            "suggestion_engine: '%s' → %d strategies (top: %s)",
            query, len(strategies), strategies[0].method if strategies else "none",
        )
        return SuggestionResult(
            query=query,
            strategies=strategies,
            intent=intent,
            app_context=app,
        )

    def publish_override(
        self,
        query: str,
        chosen_method: str,
        default_method: str,
        intent: str | None,
    ) -> None:
        """
        Called when the user explicitly selects a non-default strategy.
        Feeds the episodic memory so the learner can adapt rankings.
        """
        if chosen_method == default_method:
            return
        payload = {
            "query": query,
            "app": self._current_app,
            "intent": intent,
            "chosen_method": chosen_method,
            "default_method": default_method,
        }
        try:
            self._bus.publish("execution_strategy_overridden", payload)
            log.info(
                "suggestion_engine: override %s → %s (app=%s, intent=%s)",
                default_method, chosen_method, self._current_app, intent,
            )
        except Exception as exc:  # noqa: BLE001
            log.warning("suggestion_engine: could not publish override — %s", exc)

    # ------------------------------------------------------------------
    # Waterfall builder
    # ------------------------------------------------------------------

    def _build_waterfall(
        self,
        intent: str,
        intent_data: dict[str, Any],
        app: str,
    ) -> list[ExecutionStrategy]:
        strategies: list[ExecutionStrategy] = []
        intent_conf: float = float(intent_data.get("confidence", 0.5))

        # ── 1. Plugin strategies ──────────────────────────────────────────────
        # Always attempt the plugin registry. Log at WARNING (not debug) so a
        # silent failure is immediately visible in the panel logs.
        plugin_added = False
        try:
            plugins = self._plugin_registry(app, intent)
            log.debug(
                "suggestion_engine: plugin_registry(%r, %r) → %d result(s)",
                app, intent, len(plugins) if plugins else 0,
            )
            for plug in (plugins or []):
                strategies.append(ExecutionStrategy(
                    method="plugin",
                    label=f"{_METHOD_LABELS['plugin']} — {plug.get('name', 'unknown')}",
                    description=plug.get("description", "Execute via plugin"),
                    confidence=self._blend_confidence(
                        float(plug.get("confidence", 0.7)), intent_conf
                    ),
                    plugin_name=plug.get("name"),
                    metadata=plug,
                ))
                plugin_added = True
        except Exception as exc:  # noqa: BLE001
            log.warning(
                "suggestion_engine: plugin_registry raised — plugin strategy unavailable. "
                "Error: %s", exc,
            )

        # Guaranteed plugin fallback — shown even when no specific plugin matched.
        # This ensures the Plugin row is ALWAYS visible so the user can manually
        # choose it. Confidence is intentionally low (0.30) so it sorts below
        # any specific matches but above nothing.
        if not plugin_added:
            strategies.append(ExecutionStrategy(
                method="plugin",
                label=f"{_METHOD_LABELS['plugin']} — generic",
                description="Route to an installed plugin (no specific match found)",
                confidence=round(min(max(0.30 * intent_conf ** 0.5, 0.10), 1.0), 3),
            ))
            log.debug(
                "suggestion_engine: no plugin matched — inserted generic plugin fallback."
            )

        # ── 2. API / capability strategies ───────────────────────────────────
        try:
            caps = self._capability_registry(intent)
            log.debug(
                "suggestion_engine: capability_registry(%r) → %d result(s)",
                intent, len(caps) if caps else 0,
            )
            for cap in (caps or []):
                method = cap.get("method", "api")
                # Allow all four non-plugin methods through; coerce unknowns to api.
                if method not in ("api", "command", "shell", "ui"):
                    method = "api"
                strategies.append(ExecutionStrategy(
                    method=method,
                    label=f"{_METHOD_LABELS[method]} — {cap.get('name', intent)}",
                    description=cap.get("description", f"Execute via {method}"),
                    confidence=self._blend_confidence(
                        float(cap.get("confidence", 0.6)), intent_conf
                    ),
                    capability_id=cap.get("id"),
                    metadata=cap,
                ))
        except Exception as exc:  # noqa: BLE001
            log.warning(
                "suggestion_engine: capability_registry raised — capability strategies "
                "unavailable. Error: %s", exc,
            )

        # ── 3. Guaranteed Shell fallback ──────────────────────────────────────
        # Shell is always available — any query can be attempted as a terminal
        # command. Scored by heuristic so CLI-like queries rank it higher.
        if not any(s.method == "shell" for s in strategies):
            shell_conf = _shell_confidence(intent, intent_data)
            strategies.append(ExecutionStrategy(
                method="shell",
                label=f"{_METHOD_LABELS['shell']} — run in terminal",
                description="Execute directly as a shell command in the user's terminal",
                confidence=shell_conf,
            ))

        # ── 4. Guaranteed UI-automation fallback ─────────────────────────────
        if not any(s.method == "ui" for s in strategies):
            strategies.append(ExecutionStrategy(
                method="ui",
                label=f"{_METHOD_LABELS['ui']} — fallback",
                description="Simulate user interactions via screen automation",
                confidence=max(0.10, intent_conf * 0.5),
            ))

        # Sort: waterfall rank (plugin→api→cmd→shell→ui), then confidence desc.
        strategies.sort(key=lambda s: (s.method_rank, -s.confidence))
        log.debug(
            "suggestion_engine: final waterfall → %s",
            [(s.method, s.confidence) for s in strategies],
        )
        return strategies

    # ------------------------------------------------------------------
    # Helpers
    # ------------------------------------------------------------------

    @staticmethod
    def _blend_confidence(source_conf: float, intent_conf: float) -> float:
        """Geometric mean of two confidence scores, clamped to [0, 1]."""
        blended = (source_conf * intent_conf) ** 0.5
        return round(min(max(blended, 0.0), 1.0), 3)

    def _apply_learned_ranking(
        self,
        strategies: list[ExecutionStrategy],
        app: str,
        intent: str,
    ) -> list[ExecutionStrategy]:
        """
        If learning/learner.py has a preferred method order for this
        (app, intent) pair, re-rank strategies accordingly.
        """
        if not self._learned_ranking:
            return strategies
        try:
            learned_order = self._learned_ranking(app, intent)
            if not learned_order:
                return strategies
            rank_map = {method: idx for idx, method in enumerate(learned_order)}
            strategies.sort(key=lambda s: (
                rank_map.get(s.method, 99),
                s.method_rank,
                -s.confidence,
            ))
            log.debug(
                "suggestion_engine: applied learned ranking for (%s, %s): %s",
                app, intent, learned_order,
            )
        except Exception as exc:  # noqa: BLE001
            log.warning("suggestion_engine: learned ranking failed — %s", exc)
        return strategies