"""
Records failed mappings and proposes new intent -> capability aliases.
Persists to learning/learned_intent_aliases.json and notifies the mapper to reload.
"""
from __future__ import annotations

import json
import logging
import os
from typing import Any, Dict

from core.event_bus import bus
from capabilities.registry import capability_registry

_STORE_DIR = os.path.dirname(os.path.abspath(__file__))
_ALIAS_PATH = os.path.join(_STORE_DIR, "learned_intent_aliases.json")
_PATTERN_PATH = os.path.join(_STORE_DIR, "pattern_store.json")


class EvolutionEngine:
    def __init__(self):
        self.logger = logging.getLogger("EvolutionEngine")
        self._aliases: Dict[str, str] = {}

    def _ensure_files(self):
        os.makedirs(_STORE_DIR, exist_ok=True)
        if not os.path.isfile(_ALIAS_PATH):
            with open(_ALIAS_PATH, "w", encoding="utf-8") as f:
                json.dump({}, f)
        if not os.path.isfile(_PATTERN_PATH):
            with open(_PATTERN_PATH, "w", encoding="utf-8") as f:
                json.dump({"failures": [], "suggestions": []}, f)

    def _load_aliases(self) -> Dict[str, str]:
        self._ensure_files()
        try:
            with open(_ALIAS_PATH, "r", encoding="utf-8") as f:
                data = json.load(f)
            return {str(k): str(v) for k, v in data.items()} if isinstance(data, dict) else {}
        except (json.JSONDecodeError, OSError):
            return {}

    def _save_aliases(self, aliases: Dict[str, str]):
        self._ensure_files()
        with open(_ALIAS_PATH, "w", encoding="utf-8") as f:
            json.dump(aliases, f, indent=2, sort_keys=True)

    def _append_pattern(self, entry: Dict[str, Any]):
        self._ensure_files()
        try:
            with open(_PATTERN_PATH, "r", encoding="utf-8") as f:
                store = json.load(f)
        except (json.JSONDecodeError, OSError):
            store = {"failures": [], "suggestions": []}
        if not isinstance(store, dict):
            store = {"failures": [], "suggestions": []}
        store.setdefault("failures", []).append(entry)
        store["failures"] = store["failures"][-500:]
        with open(_PATTERN_PATH, "w", encoding="utf-8") as f:
            json.dump(store, f, indent=2)

    def _pick_target_capability(self, normalized: str, args: Dict[str, Any]) -> str | None:
        """Heuristic: infer a registry capability from loose hints in args."""
        keys = {k.lower() for k in (args or {}).keys()}
        if "path" in keys or "content" in keys or "name" in keys:
            if any(x in keys for x in ("content", "data", "text")):
                return "write_file"
            return "read_file"
        if "command" in keys or "cmd" in keys or "script_path" in keys:
            return "run_command"
        if "query" in keys or "q" in keys:
            return "search_web"
        if "url" in keys or "link" in keys:
            return "open_url"
        if "x" in keys and "y" in keys:
            return "click"
        if normalized and capability_registry.get(normalized):
            return normalized
        return None

    async def start(self):
        self._ensure_files()
        self._aliases = self._load_aliases()
        bus.subscribe("mapping_failed", self.on_mapping_failed)
        bus.subscribe("task_failed", self.on_task_failed)
        print("🧬 Evolution engine: online (alias learning enabled).")

    async def on_mapping_failed(self, event):
        data = event.data or {}
        raw = data.get("raw_intent")
        normalized = data.get("normalized")
        args = data.get("args") or {}
        if not raw:
            return

        self._append_pattern({"type": "mapping_failed", "raw_intent": raw, "normalized": normalized, "args": args})

        target = self._pick_target_capability(str(normalized or ""), args)
        if not target or target == raw:
            return
        if raw in self._aliases and self._aliases[raw] == target:
            return

        self._aliases[str(raw)] = target
        self._save_aliases(self._aliases)
        self.logger.info("Learned alias: %s -> %s", raw, target)
        await bus.emit("evolution_aliases_updated", {"raw": raw, "target": target}, source="evolution_engine")

    async def on_task_failed(self, event):
        data = event.data or {}
        err = str(data.get("error", ""))
        if "Unsupported Intent" in err or "unregistered" in err.lower():
            self._append_pattern({"type": "task_failed", "data": data})


evolution_engine = EvolutionEngine()
