import json
import logging
import os
import numpy as np
from capabilities.registry import capability_registry
from core.event_bus import bus
from core.config import settings
from brain.llm_client import llm_client # Assuming this can call Ollama's embedding endpoint

_LEARNED_PATH = os.path.join("learning", "learned_intent_aliases.json")


class CapabilityMapper:
    """🧠 Semantic Capability Mapper
    
    Uses vector embeddings to map raw user intents to actual registered
    capabilities without relying on rigid, hardcoded synonym dictionaries.
    """

    # We can keep mapping rules for arguments as a clean structure,
    # but the intent lookup itself will be entirely handled by vector math!
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
        # Stores {capability_name: np.array([embedding])}
        self.capability_vectors = {} 
        self.threshold = 0.75 # Lower = more forgiving, Higher = stricter

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

    async def _generate_capability_vectors(self):
        """Pre-calculates vectors for all active capabilities in the registry."""
        self.logger.info("📡 Pre-calculating vectors for registered capabilities...")
        
        # Pull all available capabilities from your registry
        capabilities = capability_registry.get_all_names() 
        
        for cap in capabilities:
            # We replace underscores with spaces to help the embedding model understand English meaning
            readable_cap = cap.replace("_", " ")
            self.capability_vectors[cap] = await self._get_embedding(readable_cap)
            
        self.logger.info(f"✅ Loaded {len(self.capability_vectors)} capability vectors.")

    async def _get_embedding(self, text: str) -> np.ndarray:
        """Helper to get text vector from Ollama/LLM Client."""
        try:
            # Replace this with whatever your Ollama client's embedding method is
            # typically it calls POST /api/embeddings with model="all-minilm"
            vector = await llm_client.get_embedding(text) 
            return np.array(vector)
        except Exception as e:
            self.logger.error(f"Failed to get embedding: {e}")
            return np.zeros(384) # Fallback empty vector (assuming 384 dimensions)

    def _cosine_similarity(self, v1, v2):
        """Calculates the angle between two vectors."""
        dot = np.dot(v1, v2)
        norm1 = np.linalg.norm(v1)
        norm2 = np.linalg.norm(v2)
        if norm1 == 0 or norm2 == 0:
            return 0.0
        return dot / (norm1 * norm2)

    async def normalize_intent(self, raw: str) -> str:
        r = (raw or "").strip()
        if not r:
            return r
            
        # 1. Check evolution engine manual overrides first
        if r in self.learned_aliases:
            return self.learned_aliases[r]

        # 2. Check for exact match in registry (O(1) fast pass)
        if capability_registry.get(r) is not None:
            return r

        # 3. Dynamic Vector Fallback (Semantic Search)
        self.logger.info(f"🔮 Exact match failed. Running vector lookup for: '{r}'")
        raw_vector = await self._get_embedding(r.replace("_", " "))
        
        best_match = None
        highest_score = 0.0
        
        for cap, cap_vector in self.capability_vectors.items():
            score = self._cosine_similarity(raw_vector, cap_vector)
            if score > highest_score:
                highest_score = score
                best_match = cap
                
        self.logger.info(f"🎯 Best semantic match: '{best_match}' with confidence {highest_score:.2f}")

        # Only accept the match if it breaks our safety confidence threshold
        if highest_score >= self.threshold:
            return best_match
            
        return r # Return raw if it didn't match anything safely

    def normalize_args(self, intent: str, params: dict) -> dict:
        """Standardizes argument keys dynamically."""
        p = dict(params or {})

        if intent in self.ARG_ALIASES:
            rule = self.ARG_ALIASES[intent]
            for old_key, new_key in rule.items():
                if old_key in p and new_key not in p:
                    p[new_key] = p.pop(old_key)

        if "content" in p and "data" not in p:
            p["data"] = p.pop("content")

        return p

    async def start(self):
        self._load_learned_aliases()
        
        # Build the vector cache on start
        await self._generate_capability_vectors()

        # Listen to the IntentParser after it finishes validating
        bus.subscribe("intent_validated", self.map_intent_to_capability)

        # Listen for when the evolution engine dumps new knowledge
        bus.subscribe("evolution_aliases_updated", self._on_aliases_updated)
        print("🧠 Capability Mapper: Online (Vector/Semantic backed).")

    async def _on_aliases_updated(self, _event):
        self._load_learned_aliases()
        # Re-cache vectors in case evolution engine registered a brand new capability!
        await self._generate_capability_vectors()

    async def map_intent_to_capability(self, event):
        task_id = event.data.get("task_id")
        raw_intent = event.data.get("intent")
        extracted = event.data.get("parameters") or event.data.get("data") or {}

        # 🔄 NOW ASYNC! Because vector generation requires API calls.
        normalized = await self.normalize_intent(raw_intent)
        args = self.normalize_args(normalized, extracted)

        # Quick validation check against registry
        if not normalized or capability_registry.get(normalized) is None:
            self.logger.warning(
                "Unknown or unregistered intent: %s (normalized: %s)",
                raw_intent, normalized,
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

        bus.publish(
            "capability_mapped", mapping_result, source="capability_mapper"
        )

# Global instance
capability_mapper = CapabilityMapper()