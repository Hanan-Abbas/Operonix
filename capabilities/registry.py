"""
capabilities/registry.py

Centralized registry for all capabilities.

Panel integration
─────────────────
`find(intent)` is the new public method called by the panel's suggestion
engine.  It returns a ranked list of capability dicts that match the given
intent string.  The method is synchronous so it can be called directly from
the suggestion engine's waterfall builder (which runs in asyncio.to_thread).
"""
from __future__ import annotations

import asyncio
import importlib
import logging
import pkgutil
import threading
from typing import Any

logger = logging.getLogger("CapabilityRegistry")


class CapabilityRegistry:
    """
    🧩 Centralized registry for all capabilities in the system.
    - Registers all ops (text, file, command, UI, web)
    - Provides validated, structured actions
    - Extensible and async-safe
    """

    def __init__(self) -> None:
        self.registry: dict[str, Any] = {}          # intent_name → async function
        self.metadata: dict[str, dict[str, Any]] = {}  # intent_name → metadata dict
        self.validation_rules: list = []
        self.intent_validation_rules: dict[str, list] = {}
        self._lock = threading.RLock()

    # ── Registration ──────────────────────────────────────────────────────────

    def register(self, name: str, func: Any, metadata: dict[str, Any] | None = None) -> None:
        """
        Register a capability function.

        Args:
            name:     Intent name (snake_case).
            func:     Async callable that implements the capability.
            metadata: Optional dict with 'description', 'method', 'tags', etc.
                      Used by the panel suggestion engine.
        """
        if not asyncio.iscoroutinefunction(func):
            raise ValueError(f"Capability {name} must be an async function")
        with self._lock:
            self.registry[name] = func
            self.metadata[name] = metadata or {}
        logger.info("✅ Registered capability: %s", name)

    def get(self, name: str) -> Any:
        """Retrieve a capability function by intent name."""
        return self.registry.get(name)

    def get_all_names(self) -> list[str]:
        """Returns all registered capability names (used by CapabilityMapper)."""
        return list(self.registry.keys())

    # ── Panel-facing query interface ──────────────────────────────────────────

    def find(self, intent: str) -> list[dict[str, Any]]:
        """
        Return a ranked list of capability descriptors matching *intent*.

        Called synchronously by the panel's SuggestionEngine waterfall builder.
        Resolution:
          1. Exact name match  → confidence 0.95
          2. Prefix / substring match → confidence scaled by overlap
          3. Returns [] if nothing matches (UI-fallback takes over)

        Each returned dict has:
          name, description, method ("api" | "command" | "ui"), confidence, id
        """
        results: list[dict[str, Any]] = []
        intent_lower = intent.lower() if intent else ""

        with self._lock:
            for cap_name, func in self.registry.items():
                meta = self.metadata.get(cap_name, {})
                cap_lower = cap_name.lower()

                if cap_lower == intent_lower:
                    confidence = 0.95
                elif intent_lower and intent_lower in cap_lower:
                    # Proportional: longer match = higher score
                    confidence = round(0.5 + 0.4 * (len(intent_lower) / len(cap_lower)), 3)
                elif intent_lower and cap_lower in intent_lower:
                    confidence = round(0.4 + 0.3 * (len(cap_lower) / max(len(intent_lower), 1)), 3)
                else:
                    # Tag / description fuzzy match
                    description = meta.get("description", "")
                    tags = " ".join(meta.get("tags", []))
                    haystack = f"{cap_lower} {description.lower()} {tags.lower()}"
                    if intent_lower and intent_lower in haystack:
                        confidence = 0.3
                    else:
                        continue

                results.append({
                    "id":          cap_name,
                    "name":        cap_name,
                    "description": meta.get("description", f"Execute {cap_name}"),
                    # Capabilities declare their preferred execution method via metadata.
                    # Default to "api" which maps to direct Python function call.
                    "method":      meta.get("method", "api"),
                    "confidence":  confidence,
                    "tags":        meta.get("tags", []),
                })

        # Sort by confidence descending, then alphabetically for ties.
        results.sort(key=lambda r: (-r["confidence"], r["name"]))
        return results

    # ── Validation ────────────────────────────────────────────────────────────

    def add_validation_rule(self, rule_func: Any) -> None:
        self.validation_rules.append(rule_func)
        logger.info("✅ Added validation rule: %s", rule_func.__name__)

    def add_intent_validation(self, intent_name: str, rule_func: Any) -> None:
        self.intent_validation_rules.setdefault(intent_name, []).append(rule_func)
        logger.info("✅ Validation for '%s': %s", intent_name, rule_func.__name__)

    def list_registered(self) -> list[str]:
        return sorted(self.registry.keys())

    async def validate(self, intent_name: str, action_data: dict, args: dict | None = None) -> tuple[bool, str | None]:
        merged = {**(args or {}), **(action_data.get("args") or {})}
        for rule in self.intent_validation_rules.get(intent_name, []):
            result, msg = await self._maybe_async(rule, action_data, merged)
            if not result:
                logger.warning("❌ Validation failed: %s - %s", rule.__name__, msg)
                return False, msg
        for rule in self.validation_rules:
            result, msg = await self._maybe_async(rule, action_data, merged)
            if not result:
                logger.warning("❌ Validation failed: %s - %s", rule.__name__, msg)
                return False, msg
        return True, None

    @staticmethod
    async def _maybe_async(func: Any, *args: Any, **kwargs: Any) -> Any:
        if asyncio.iscoroutinefunction(func):
            return await func(*args, **kwargs)
        return func(*args, **kwargs)

    # ── Unified execution interface ───────────────────────────────────────────

    async def execute(self, intent_name: str, context: dict, args: dict) -> tuple[bool, Any]:
        """
        Unified interface to execute a capability.
        Returns (success: bool, action_data or error_message).
        """
        capability = self.get(intent_name)
        if capability is None:
            msg = f"No capability registered for intent: {intent_name}"
            logger.error("❌ %s", msg)
            return False, msg

        try:
            action_data = await capability(context, args)
            logger.info("🎯 Executed capability: %s", intent_name)
        except Exception as exc:
            msg = f"Error executing capability {intent_name}: {exc}"
            logger.error("❌ %s", msg)
            return False, msg

        valid, error_msg = await self.validate(intent_name, action_data, args)
        if not valid:
            msg = f"Validation failed for {intent_name}: {error_msg}"
            logger.error("❌ %s", msg)
            return False, msg

        return True, action_data

    # ── Auto-registration ─────────────────────────────────────────────────────

    def auto_register_ops(self, ops_package: Any) -> None:
        """
        Automatically imports all *_ops.py modules in a given package
        and registers async functions as capabilities.
        """
        package_path = ops_package.__path__[0]

        for _loader, module_name, _is_pkg in pkgutil.iter_modules([package_path]):
            if not module_name.endswith("_ops"):
                continue
            full_module_name = f"{ops_package.__name__}.{module_name}"
            module = importlib.import_module(full_module_name)

            for attr_name in dir(module):
                if attr_name.startswith("_"):
                    continue
                if any(attr_name.startswith(p) for p in ("validate_", "normalize_", "parse_")):
                    continue
                if attr_name.endswith("_check"):
                    continue

                attr = getattr(module, attr_name)
                if asyncio.iscoroutinefunction(attr):
                    # Pull metadata from the function's __capability_meta__ attribute
                    # if the ops module decorated it; otherwise use an empty dict.
                    meta = getattr(attr, "__capability_meta__", {})
                    self.register(attr_name, attr, metadata=meta)


# Global instance
capability_registry = CapabilityRegistry()