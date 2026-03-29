import json
import logging
import os
from core.event_bus import bus
from capabilities.registry import capability_registry

_LEARNED_PATH = os.path.join("learning", "learned_intent_aliases.json")


class CapabilityMapper:
    """
    Maps LLM / user intents to registered capability names (registry keys).
    Learned aliases are merged from learning/learned_intent_aliases.json (written by evolution_engine).
    """

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
                self.learned_aliases = {str(k): str(v) for k, v in data.items()}
        except (json.JSONDecodeError, OSError) as e:
            self.logger.warning("Could not load learned aliases: %s", e)

    def normalize_intent(self, raw: str) -> str:
        r = (raw or "").strip()
        if not r:
            return r
        if r in self.learned_aliases:
            return self.learned_aliases[r]
        return self.SYNONYMS.get(r, r)

    def normalize_args(self, intent: str, params: dict) -> dict:
        p = dict(params or {})
        if intent == "write_file" and "path" not in p and p.get("name"):
            p["path"] = p.pop("name")
        if intent == "run_command" and "command" not in p and p.get("cmd"):
            p["command"] = p.pop("cmd")
        if intent == "search_web" and "query" not in p and p.get("q"):
            p["query"] = p.pop("q")
        if intent == "open_url" and "url" not in p and p.get("link"):
            p["url"] = p.pop("link")
        if intent == "run_command" and "command" not in p and p.get("app"):
            p["command"] = str(p.pop("app"))
        return p

    async def start(self):
        self._load_learned_aliases()
        bus.subscribe("intent_parsed", self.map_intent_to_capability)
        bus.subscribe("evolution_aliases_updated", self._on_aliases_updated)
        print("🧠 Capability Mapper: Online (registry-backed).")

    async def _on_aliases_updated(self, _event):
        self._load_learned_aliases()

    async def map_intent_to_capability(self, event):
        task_id = event.data.get("task_id")
        raw_intent = event.data.get("intent")
        extracted = event.data.get("parameters") or event.data.get("data") or {}

        normalized = self.normalize_intent(raw_intent)
        args = self.normalize_args(normalized, extracted)

        if not normalized or capability_registry.get(normalized) is None:
            self.logger.warning("Unknown or unregistered intent: %s (normalized: %s)", raw_intent, normalized)
            await bus.emit(
                "mapping_failed",
                {"task_id": task_id, "raw_intent": raw_intent, "normalized": normalized, "args": args},
                source="capability_mapper",
            )
            return

        mapping_result = {
            "task_id": task_id,
            "intent": normalized,
            "capability": normalized,
            "suggested_tool": None,
            "args": args,
        }

        self.logger.info("Mapped '%s' -> '%s'", raw_intent, normalized)
        await bus.emit("capability_mapped", mapping_result, source="capability_mapper")


capability_mapper = CapabilityMapper()
