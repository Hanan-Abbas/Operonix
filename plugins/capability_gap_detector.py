"""
plugins/capability_gap_detector.py

Detects capability gaps by monitoring the event bus for task failures.

Trigger logic (per spec):
  - 3 consecutive failures of the same intent, OR
  - 5 failures within 24 hours of the same intent

Semantic grouping:
  - "open chrome" and "launch browser" are treated as the same intent
    by using embedding similarity via llm_client.get_embedding()

On gap detected:
  - Publishes "capability_gap_detected" event
  - generator.py subscribes to this and starts the generation pipeline
"""
from __future__ import annotations

import asyncio
import logging
import time
from collections import defaultdict

import numpy as np

from core.config import settings
from core.event_bus import bus
from memory.episodic import episodic_memory

logger = logging.getLogger("CapabilityGapDetector")

# Thresholds (per spec — override via settings if needed)
CONSECUTIVE_FAIL_THRESHOLD: int = int(
    getattr(settings, "GAP_CONSECUTIVE_THRESHOLD", 3)
)
WINDOW_FAIL_THRESHOLD: int = int(
    getattr(settings, "GAP_WINDOW_THRESHOLD", 5)
)
WINDOW_HOURS: int = int(getattr(settings, "GAP_WINDOW_HOURS", 24))

# Semantic similarity threshold for grouping similar intents
SEMANTIC_GROUP_THRESHOLD: float = float(
    getattr(settings, "GAP_SEMANTIC_THRESHOLD", 0.82)
)


class CapabilityGapDetector:
    """
    Monitors task failures and detects when the agent is missing a capability.

    Subscribes to:
      - task_failed
      - mapping_failed  (intent didn't map to any capability)
      - plugin_validation_failed  (generation failed — avoid infinite loops)

    Publishes:
      - capability_gap_detected  →  generator.py consumes this
    """

    def __init__(self):
        self.logger = logging.getLogger("CapabilityGapDetector")

        # In-memory consecutive failure counters: intent → count
        self._consecutive: dict[str, int] = defaultdict(int)

        # Cache of already-triggered gaps to avoid duplicate generation
        # intent → timestamp of last trigger
        self._triggered: dict[str, float] = {}

        # Semantic embedding cache: intent → np.array
        self._embeddings: dict[str, np.ndarray] = {}

        # Canonical intent groups: canonical_intent → [alias_intents]
        self._groups: dict[str, list[str]] = {}

        # Intents blocked from generation (failed too many times)
        self._blocked: set[str] = set()

        # Re-trigger cooldown: don't trigger the same gap within N seconds
        self._cooldown_seconds: float = float(
            getattr(settings, "GAP_COOLDOWN_SECONDS", 3600)
        )

    async def start(self):
        """Subscribe to failure events."""
        bus.subscribe("task_failed",              self._on_task_failed)
        bus.subscribe("mapping_failed",           self._on_mapping_failed)
        bus.subscribe("plugin_validation_failed", self._on_plugin_generation_failed)

        self.logger.info(
            "🔎 Capability Gap Detector: Active. "
            f"Thresholds: {CONSECUTIVE_FAIL_THRESHOLD} consecutive / "
            f"{WINDOW_FAIL_THRESHOLD} in {WINDOW_HOURS}h"
        )

    # ── Event Handlers ─────────────────────────────────────────────────────────

    async def _on_task_failed(self, event):
        data   = event.data or {}
        intent = data.get("intent") or data.get("capability", "")
        reason = data.get("error") or data.get("reason", "Unknown")

        if not intent:
            return

        await self._process_failure(intent, reason)

    async def _on_mapping_failed(self, event):
        """
        Fires when CapabilityMapper cannot map an intent at all.
        This is a strong signal of a missing capability.
        """
        data        = event.data or {}
        raw_intent  = data.get("raw_intent", "")
        normalized  = data.get("normalized", "")
        intent      = normalized or raw_intent

        if not intent:
            return

        self.logger.info(
            f"🔍 Unmapped intent detected: '{raw_intent}' → "
            f"triggering gap analysis."
        )
        await self._process_failure(intent, "No capability registered for this intent")

    async def _on_plugin_generation_failed(self, event):
        """
        If plugin generation itself fails repeatedly, block the intent
        to prevent an infinite generation loop.
        """
        data   = event.data or {}
        name   = data.get("name", "")
        stage  = data.get("stage", "")

        # We don't have the intent here, but we can infer from plugin name
        # (plugin names are derived from intent in generator.py)
        # Mark as blocked after repeated LLM audit failures
        if stage == "llm_audit" and name:
            self._blocked.add(name)
            self.logger.warning(
                f"⛔ Intent '{name}' blocked from generation (repeated LLM audit failures)."
            )

    # ── Core Logic ─────────────────────────────────────────────────────────────

    async def _process_failure(self, intent: str, reason: str):
        """
        Increments failure counters and checks if a gap should be triggered.
        Uses semantic grouping to merge similar intents.
        """
        # Resolve to canonical intent via semantic grouping
        canonical = await self._resolve_canonical(intent)

        if canonical in self._blocked:
            self.logger.debug(f"Skipping blocked intent: '{canonical}'")
            return

        # Increment in-memory consecutive counter
        self._consecutive[canonical] += 1
        consecutive = self._consecutive[canonical]

        # Also query episodic DB for window-based count
        window_count = episodic_memory.get_failures_in_window(
            canonical, hours=WINDOW_HOURS
        )

        self.logger.debug(
            f"Failure tracked: '{canonical}' | consecutive={consecutive} | "
            f"window_{WINDOW_HOURS}h={window_count}"
        )

        # Check thresholds
        should_trigger = (
            consecutive >= CONSECUTIVE_FAIL_THRESHOLD
            or window_count >= WINDOW_FAIL_THRESHOLD
        )

        if should_trigger:
            await self._trigger_gap(canonical, reason, consecutive, window_count)

    async def _trigger_gap(
        self, intent: str, reason: str, consecutive: int, window_count: int
    ):
        """Publishes the capability_gap_detected event."""
        # Cooldown check
        last_trigger = self._triggered.get(intent, 0)
        if time.time() - last_trigger < self._cooldown_seconds:
            self.logger.debug(
                f"Gap for '{intent}' is in cooldown ({self._cooldown_seconds}s). Skipping."
            )
            return

        # Check plugin_memory to avoid regenerating an already-failed plugin
        try:
            from plugins.plugin_memory import plugin_memory
            if plugin_memory.already_attempted(intent):
                self.logger.warning(
                    f"⚠️ Plugin for '{intent}' already attempted and failed 3+ times. "
                    f"Blocking further generation."
                )
                self._blocked.add(intent)
                return
        except Exception as e:
            self.logger.debug(f"Could not check plugin_memory: {e}")

        self._triggered[intent] = time.time()
        self._consecutive[intent] = 0  # Reset counter after trigger

        # Build failure summary from episodic memory
        failure_summary = episodic_memory.get_failure_summary(intent)

        self.logger.warning(
            f"🚨 CAPABILITY GAP DETECTED: '{intent}' | "
            f"consecutive={consecutive} | window={window_count}"
        )

        bus.publish(
            "capability_gap_detected",
            {
                "intent": intent,
                "reason": reason,
                "consecutive_failures": consecutive,
                "window_failures": window_count,
                "failure_summary": failure_summary,
            },
            source="capability_gap_detector",
        )

    # ── Semantic Grouping ──────────────────────────────────────────────────────

    async def _resolve_canonical(self, intent: str) -> str:
        """
        Resolves an intent to its canonical form using embedding similarity.
        "open chrome" and "launch browser" will map to the same canonical intent.
        Returns the canonical intent string.
        """
        if not intent:
            return intent

        # Normalize
        normalized = intent.lower().replace("_", " ").strip()

        # Check if we already know this intent
        if normalized in self._embeddings:
            return self._find_canonical_for(normalized)

        # Get embedding for the new intent
        embedding = await self._get_embedding(normalized)
        if embedding is None:
            return intent  # Fallback: use as-is

        self._embeddings[normalized] = embedding

        # Check against all known canonical intents
        best_canonical = None
        best_score = 0.0

        for known_intent, known_vec in self._embeddings.items():
            if known_intent == normalized:
                continue
            score = self._cosine_similarity(embedding, known_vec)
            if score > best_score:
                best_score = score
                best_canonical = known_intent

        if best_canonical and best_score >= SEMANTIC_GROUP_THRESHOLD:
            # Group this intent under the existing canonical
            canonical = self._find_canonical_for(best_canonical)
            if canonical not in self._groups:
                self._groups[canonical] = []
            if normalized not in self._groups[canonical]:
                self._groups[canonical].append(normalized)
            self.logger.info(
                f"🔗 Grouped '{intent}' → '{canonical}' "
                f"(similarity={best_score:.2f})"
            )
            return canonical

        # New unique intent — it becomes its own canonical
        return normalized

    def _find_canonical_for(self, intent: str) -> str:
        """Find the canonical key for an intent (may be aliased)."""
        for canonical, aliases in self._groups.items():
            if intent in aliases:
                return canonical
        return intent

    async def _get_embedding(self, text: str) -> np.ndarray | None:
        """Get embedding vector via llm_client."""
        try:
            from brain.llm_client import llm_client
            vector = await llm_client.get_embedding(text)
            if vector:
                return np.array(vector)
        except Exception as e:
            self.logger.debug(f"Embedding failed for '{text}': {e}")
        return None

    @staticmethod
    def _cosine_similarity(v1: np.ndarray, v2: np.ndarray) -> float:
        """Standard cosine similarity between two vectors."""
        if v1 is None or v2 is None:
            return 0.0
        norm1 = np.linalg.norm(v1)
        norm2 = np.linalg.norm(v2)
        if norm1 == 0 or norm2 == 0:
            return 0.0
        return float(np.dot(v1, v2) / (norm1 * norm2))


# Global instance
capability_gap_detector = CapabilityGapDetector()