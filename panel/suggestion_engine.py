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

    method: str          # "plugin" | "api" | "command" | "ui"
    label: str           # human-readable label, e.g. "VS Code Plugin — open file"
    description: str     # what will actually happen
    confidence: float    # 0.0 – 1.0
    plugin_name: str | None = None
    capability_id: str | None = None
    metadata: dict[str, Any] = field(default_factory=dict)

    @property
    def method_rank(self) -> int:
        """Lower = preferred in the waterfall."""
        return {"plugin": 0, "api": 1, "command": 2, "ui": 3}.get(self.method, 99)


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
# Waterfall logic
# ---------------------------------------------------------------------------

_METHOD_LABELS = {
    "plugin":  "Plugin",
    "api":     "API",
    "command": "Command",
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

        # 1. Plugin strategies
        try:
            plugins = self._plugin_registry(app, intent)
            for plug in plugins:
                strategies.append(ExecutionStrategy(
                    method="plugin",
                    label=f"{_METHOD_LABELS['plugin']} — {plug.get('name', 'unknown')}",
                    description=plug.get("description", "Execute via plugin"),
                    confidence=self._blend_confidence(
                        plug.get("confidence", 0.7), intent_data.get("confidence", 0.5)
                    ),
                    plugin_name=plug.get("name"),
                    metadata=plug,
                ))
        except Exception as exc:  # noqa: BLE001
            log.debug("suggestion_engine: plugin lookup failed — %s", exc)

        # 2. API / capability strategies
        try:
            caps = self._capability_registry(intent)
            for cap in caps:
                method = cap.get("method", "api")  # capability reports its own method
                if method not in ("api", "command", "ui"):
                    method = "api"
                strategies.append(ExecutionStrategy(
                    method=method,
                    label=f"{_METHOD_LABELS[method]} — {cap.get('name', intent)}",
                    description=cap.get("description", f"Execute via {method}"),
                    confidence=self._blend_confidence(
                        cap.get("confidence", 0.6), intent_data.get("confidence", 0.5)
                    ),
                    capability_id=cap.get("id"),
                    metadata=cap,
                ))
        except Exception as exc:  # noqa: BLE001
            log.debug("suggestion_engine: capability lookup failed — %s", exc)

        # 3. Ensure the waterfall always has a UI-automation fallback.
        if not any(s.method == "ui" for s in strategies):
            strategies.append(ExecutionStrategy(
                method="ui",
                label=f"{_METHOD_LABELS['ui']} — fallback",
                description="Simulate user interactions via screen automation",
                confidence=max(0.1, intent_data.get("confidence", 0.3) * 0.5),
            ))

        # Sort: first by waterfall rank (plugin→api→cmd→ui), then by confidence desc.
        strategies.sort(key=lambda s: (s.method_rank, -s.confidence))
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