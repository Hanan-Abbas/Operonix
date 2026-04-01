import json
import logging
import os
from capabilities.registry import capability_registry
from core.event_bus import bus

# Path where the evolution engine saves its discovered synonyms
_LEARNED_PATH = os.path.join("learning", "learned_intent_aliases.json")


class CapabilityMapper:
    """Maps LLM / user intents to registered capability names (registry keys).

    Learned aliases are merged from learning/learned_intent_aliases.json
    (written by evolution_engine).
    """

    # Static fallbacks for core system intents
    SYNONYMS = {
        "file_create": "write_file",
        "create_file": "write_file",
        "new_file": "write_file",
        "file_delete": "delete_file",
        "remove_file": "delete_file",
        "shell_command": "run_command",
        "run_terminal": "run_command",
        "terminal": "run_command",
        "ui_click": "click",
        "click_element": "click",
        "ui_type": "type_text",
        "browser_open": "open_url",
        "open_browser": "open_url",
        "web_search": "search_web",
        "app_launch": "run_command",
        "open_app": "run_command",
        "write_code": "write_file",
        "ui_interact": "click",
    }

    # 🔄 UPGRADE: Dynamic argument normalization map instead of hardcoded IFs
    ARG_ALIASES = {
        "write_file": {"name": "path", "content": "data"},
        "run_command": {"cmd": "command", "app": "command"},
        "search_web": {"q": "query"},
        "open_url": {"link": "url"},
        "move_file": {"src": "path", "dst": "destination"},
    }

    def __init__(self):
        self.logger = logging.getLogger("CapabilityMapper")
        self.learned_aliases = {}

    def _load_learned_aliases(self):
        self.learned_aliases = {}
        if not os.path.isfile(_LEARNED_PATH):
            return
        try:
            with open(_LEARNED_PATH, "r", encoding="utf-8") as f:
                data = json.load(f)
            if isinstance(data, dict):
                self.learned_aliases = {
                    str(k): str(v) for k, v in data.items()
                }
        except (json.JSONDecodeError, OSError) as e:
            self.logger.warning("Could not load learned aliases: %s", e)

    def normalize_intent(self, raw: str) -> str:
        r = (raw or "").strip()
        if not r:
            return r
        if r in self.learned_aliases:
            return self.learned_aliases[r]

        # Check if it hits our hardcoded base synonyms
        if r in self.SYNONYMS:
            return self.SYNONYMS[r]

        # 🔄 UPGRADE: Dynamic prefix matching!
        # If it's a completely new action (like 'edit_file'), we can infer it
        if r.startswith("create_") or r.startswith("make_"):
            return f"write_{r.split('_', 1)[1]}"

        return r

    def normalize_args(self, intent: str, params: dict) -> dict:
        """Standardizes argument keys dynamically."""
        p = dict(params or {})

        # 🔄 UPGRADE: Apply mapped transformations dynamically
        if intent in self.ARG_ALIASES:
            rule = self.ARG_ALIASES[intent]
            for old_key, new_key in rule.items():
                if old_key in p and new_key not in p:
                    # Move data over to the standardized key
                    p[new_key] = p.pop(old_key)

        # Catch remaining floating standard keys (fallback rule)
        if "content" in p and "data" not in p:
            p["data"] = p.pop("content")

        return p

    async def start(self):
        self._load_learned_aliases()

        # Listen to the IntentParser after it finishes validating
        bus.subscribe("intent_validated", self.map_intent_to_capability)

        # Listen for when the evolution engine dumps new knowledge
        bus.subscribe("evolution_aliases_updated", self._on_aliases_updated)
        print("🧠 Capability Mapper: Online (registry-backed).")

    async def _on_aliases_updated(self, _event):
        self._load_learned_aliases()

    async def map_intent_to_capability(self, event):
        task_id = event.data.get("task_id")
        raw_intent = event.data.get("intent")
        extracted = (
            event.data.get("parameters") or event.data.get("data") or {}
        )

        normalized = self.normalize_intent(raw_intent)
        args = self.normalize_args(normalized, extracted)

        # Quick validation check against registry
        if not normalized or capability_registry.get(normalized) is None:
            self.logger.warning(
                "Unknown or unregistered intent: %s (normalized: %s)",
                raw_intent,
                normalized,
            )

            bus.publish(
                "mapping_failed",
                {
                    "task_id": task_id,
                    "raw_intent": raw_intent,
                    "normalized": normalized,
                    "args": args,
                },
                source="capability_mapper",
            )
            return

        mapping_result = {
            "task_id": task_id,
            "intent": normalized,
            "capability": normalized,
            "suggested_tool": None,
            "parameters": args,
        }

        self.logger.info("Mapped '%s' -> '%s'", raw_intent, normalized)

        # Fire the exact event that the Decision Engine is waiting for!
        bus.publish(
            "capability_mapped", mapping_result, source="capability_mapper"
        )


# Global instance
capability_mapper = CapabilityMapper()