"""
brain/capability_mapper.py
───────────────────────────
Semantic Capability Mapper.

Changes from original
──────────────────────
BUG FIX — _resolve_suggested_tool()
    Previously read capability_registry.metadata which was always empty
    because no *_ops.py module was populating it.

    Now:
    1. First reads capability_registry.metadata (existing hook — works if
       bootstrap.py uses @capability_meta decorators in the future).
    2. Falls back to reading the CAPABILITY_METADATA dict that each *_ops.py
       module now exports directly.  This keeps zero hardcoding in this file —
       the ops modules own their own tool preferences.

BUG FIX — normalize_args()
    create_dir was receiving {dir_name, location}.  The ARG_ALIASES table now
    includes a rule so "dir_name" is preserved as-is (file_ops._resolve_path
    understands it) and "location" is also preserved.  No lossy rewrites.

No other logic changed.
"""
from __future__ import annotations

import importlib
import json
import logging
import os
from typing import Optional

import numpy as np

from capabilities.registry import capability_registry
from core.event_bus import bus
from brain.llm_client import llm_client

_LEARNED_PATH = os.path.join("learning", "learned_intent_aliases.json")

# Ops modules that export CAPABILITY_METADATA.
# Add new *_ops modules here as they are created — zero other changes needed.
_OPS_MODULES: list[str] = [
    "capabilities.file_ops",
    "capabilities.command_ops",
    "capabilities.web_ops",
    "capabilities.text_ops",
    "capabilities.ui_ops",
]


class CapabilityMapper:
    """
    Uses vector embeddings to map raw user intents to actual registered
    capabilities without relying on rigid, hardcoded synonym dictionaries.
    """

    ARG_ALIASES: dict[str, dict[str, str]] = {
        "write_file":  {"name": "path", "content": "data"},
        "run_command": {"cmd": "command", "app": "command"},
        "search_web":  {"q": "query"},
        "open_url":    {"link": "url"},
        "move_file":   {"src": "path", "dst": "destination"},
        "create_dir":  {"name": "dir_name"},
        "delete_dir":  {"name": "dir_name"},
    }

    def __init__(self) -> None:
        self.logger = logging.getLogger("CapabilityMapper")
        self.learned_aliases: dict[str, str] = {}
        self.capability_vectors: dict[str, np.ndarray] = {}
        self.threshold = 0.75
        # Merged metadata from all ops modules + registry
        self._ops_metadata: dict[str, dict] = {}

    # ── Startup ──────────────────────────────────────────────────────── #

    async def start(self) -> None:
        self._load_learned_aliases()
        self._load_ops_metadata()          # NEW — load CAPABILITY_METADATA
        await self._generate_capability_vectors()
        bus.subscribe("intent_validated", self.map_intent_to_capability)
        bus.subscribe("evolution_aliases_updated", self._on_aliases_updated)
        self.logger.info("Capability Mapper: Online (Vector/Semantic backed).")

    # ── Ops metadata loader ───────────────────────────────────────────── #

    def _load_ops_metadata(self) -> None:
        """
        Import each *_ops module and merge its CAPABILITY_METADATA dict.
        Completely dynamic — no per-intent hardcoding here.
        """
        self._ops_metadata = {}
        for module_path in _OPS_MODULES:
            try:
                mod = importlib.import_module(module_path)
                meta: dict = getattr(mod, "CAPABILITY_METADATA", {})
                self._ops_metadata.update(meta)
                self.logger.debug(
                    "Loaded %d metadata entries from %s", len(meta), module_path
                )
            except ImportError:
                pass  # Module not yet written — skip gracefully
            except Exception as exc:
                self.logger.warning("Could not load metadata from %s: %s", module_path, exc)

        self.logger.info(
            "Ops metadata loaded: %d capabilities with explicit tool assignments",
            len(self._ops_metadata),
        )

    # ── Learned aliases ───────────────────────────────────────────────── #

    def _load_learned_aliases(self) -> None:
        self.learned_aliases = {}
        if not os.path.isfile(_LEARNED_PATH):
            return
        try:
            with open(_LEARNED_PATH, "r", encoding="utf-8") as fh:
                data = json.load(fh)
            if isinstance(data, dict):
                self.learned_aliases = {str(k): str(v) for k, v in data.items()}
        except (json.JSONDecodeError, OSError) as exc:
            self.logger.warning("Could not load learned aliases: %s", exc)

    # ── Vector helpers ────────────────────────────────────────────────── #

    async def _generate_capability_vectors(self) -> None:
        self.logger.info("Pre-calculating vectors for registered capabilities...")
        for cap in capability_registry.get_all_names():
            readable = cap.replace("_", " ")
            self.capability_vectors[cap] = await self._get_embedding(readable)
        self.logger.info("Loaded %d capability vectors.", len(self.capability_vectors))

    async def _get_embedding(self, text: str) -> np.ndarray:
        try:
            vector = await llm_client.get_embedding(text)
            return np.array(vector)
        except Exception as exc:
            self.logger.error("Failed to get embedding: %s", exc)
            return np.zeros(384)

    @staticmethod
    def _cosine_similarity(v1: np.ndarray, v2: np.ndarray) -> float:
        dot = np.dot(v1, v2)
        n1, n2 = np.linalg.norm(v1), np.linalg.norm(v2)
        if n1 == 0 or n2 == 0:
            return 0.0
        return float(dot / (n1 * n2))

    # ── Intent normalisation ──────────────────────────────────────────── #

    async def normalize_intent(self, raw: str) -> str:
        r = (raw or "").strip()
        if not r:
            return r
        if r in self.learned_aliases:
            return self.learned_aliases[r]
        if capability_registry.get(r) is not None:
            return r

        self.logger.info("Exact match failed. Running vector lookup for: '%s'", r)
        raw_vector = await self._get_embedding(r.replace("_", " "))

        best_match: Optional[str] = None
        highest_score = 0.0
        for cap, cap_vector in self.capability_vectors.items():
            score = self._cosine_similarity(raw_vector, cap_vector)
            if score > highest_score:
                highest_score = score
                best_match = cap

        self.logger.info(
            "Best semantic match: '%s' (confidence=%.2f)", best_match, highest_score
        )
        if highest_score >= self.threshold:
            return best_match  # type: ignore[return-value]
        return r

    def normalize_args(self, intent: str, params: dict) -> dict:
        p = dict(params or {})
        if intent in self.ARG_ALIASES:
            for old_key, new_key in self.ARG_ALIASES[intent].items():
                if old_key in p and new_key not in p:
                    p[new_key] = p.pop(old_key)
        # Generic content->data alias for write operations
        if "content" in p and "data" not in p and intent not in ("create_dir", "delete_dir"):
            p["data"] = p.pop("content")
        return p

    # ── Tool resolution (BUG FIX) ─────────────────────────────────────── #

    def _resolve_suggested_tool(self, capability_name: str) -> Optional[str]:
        """
        Read the preferred execution tool for a capability.

        Priority:
          1. capability_registry.metadata  (set via @capability_meta decorators)
          2. CAPABILITY_METADATA dicts exported by each *_ops.py module
          3. None  (DecisionEngine prefix heuristic fires as safety net)
        """
        # 1. Registry metadata (decorator-based, future-proof)
        registry_meta = capability_registry.metadata.get(capability_name, {})
        tool_from_registry = registry_meta.get("tool")
        if tool_from_registry:
            return tool_from_registry

        # 2. Ops module metadata (current source of truth)
        ops_meta = self._ops_metadata.get(capability_name, {})
        tool_from_ops = ops_meta.get("tool")
        if tool_from_ops:
            self.logger.debug(
                "Resolved tool for '%s' from ops metadata: %s",
                capability_name, tool_from_ops,
            )
            return tool_from_ops

        # 3. No explicit mapping — let DecisionEngine heuristic handle it
        self.logger.debug("No explicit tool metadata for '%s'", capability_name)
        return None

    # ── Plugin intent lookup ─────────────────────────────────────────── #

    def _find_plugin_for_intent(self, intent: str) -> str | None:
        """
        Check plugin_registry for a plugin whose capabilities match intent.
        Returns the matched capability string, or None if no plugin found.
        Uses the same fuzzy matching as intent_parser Tier 0.
        """
        try:
            from plugins.registry import plugin_registry
            from brain.intent_matcher import match_intent_local

            normalized = intent.lower().replace("_", " ").strip()
            cap_map: dict[str, str] = {}

            for pname, entry in plugin_registry.entries.items():
                caps = getattr(entry.manifest, "capabilities", []) or []
                for cap in caps:
                    cap_str = str(cap).lower().replace("_", " ")
                    cap_map[cap_str] = cap_str
                cap_map[pname.replace("_", " ")] = pname.replace("_", " ")

            if not cap_map:
                return None

            threshold = float(getattr(
                __import__("core.config", fromlist=["settings"]).settings,
                "PLUGIN_INTENT_MATCH_THRESHOLD", 0.55
            ))
            matched, score = match_intent_local(
                normalized, list(cap_map.keys()), threshold=threshold
            )

            # Substring fallback — catches vague verbs like "click" in "auto clicker"
            if not matched:
                for cap in cap_map:
                    if (normalized in cap or cap in normalized) and len(normalized) >= 3:
                        matched = cap
                        break

            if matched:
                self.logger.debug(
                    "Plugin intent match: '%s' → '%s' (score=%.2f)",
                    intent, matched, score,
                )
                return matched

        except Exception as exc:
            self.logger.debug("Plugin intent lookup failed (non-fatal): %s", exc)

        return None

    # ── Main handler ──────────────────────────────────────────────────── #

    async def map_intent_to_capability(self, event: object) -> None:
        task_id = event.data.get("task_id")
        raw_intent = event.data.get("intent")
        extracted = event.data.get("parameters") or event.data.get("data") or {}

        normalized = await self.normalize_intent(raw_intent)
        args = self.normalize_args(normalized, extracted)

        # ── Plugin registry check ─────────────────────────────────────────────
        # plugin_registry is separate from capability_registry.
        # Before firing mapping_failed, check if a loaded plugin handles
        # this intent — if so, route it as capability_mapped so the executor
        # can dispatch it via the direct plugin dispatch block.
        if not normalized or capability_registry.get(normalized) is None:
            plugin_match = self._find_plugin_for_intent(normalized or raw_intent)
            if plugin_match:
                # A plugin handles this intent — map it directly
                suggested_tool = "plugin"
                mapping_result = {
                    "task_id":       task_id,
                    "intent":        plugin_match,
                    "capability":    plugin_match,
                    "suggested_tool": suggested_tool,
                    "parameters":    args,
                }
                self.logger.info(
                    "Mapped '%s' → plugin capability '%s'",
                    raw_intent, plugin_match,
                )
                bus.publish("capability_mapped", mapping_result, source="capability_mapper")
                return

            self.logger.warning(
                "Unknown or unregistered intent: %s (normalized: %s)",
                raw_intent, normalized,
            )
            bus.publish(
                "mapping_failed",
                {
                    "task_id":    task_id,
                    "raw_intent": raw_intent,
                    "normalized": normalized,
                    "args":       args,
                },
                source="capability_mapper",
            )
            return

        suggested_tool = self._resolve_suggested_tool(normalized)

        mapping_result = {
            "task_id": task_id,
            "intent": normalized,
            "capability": normalized,
            "suggested_tool": suggested_tool,
            "parameters": args,
        }

        self.logger.info(
            "Mapped '%s' -> '%s' (tool=%s)", raw_intent, normalized, suggested_tool
        )
        bus.publish("capability_mapped", mapping_result, source="capability_mapper")

    # ── Event callbacks ───────────────────────────────────────────────── #

    async def _on_aliases_updated(self, _event: object) -> None:
        self._load_learned_aliases()
        self._load_ops_metadata()
        await self._generate_capability_vectors()


capability_mapper = CapabilityMapper()