import json
import logging
import os
from core.config import settings


class PatternPruner:
    """✂️ The Memory Optimizer of the Learning System.

    Prevents 'pattern_store.json' from growing too large by removing duplicate,
    least-used, or highly inefficient execution patterns.
    """

    def __init__(self, store_path="learning/pattern_store.json"):
        self.logger = logging.getLogger("PatternPruner")
        self.store_path = store_path

    async def prune_store(self):
        """Loads the store, applies cleanup rules, and saves the optimized

        result.
        """
        if not os.path.exists(self.store_path):
            return

        try:
            with open(self.store_path, "r") as f:
                data = json.load(f)

            patterns = data.get("patterns", {})
            optimized_patterns = {}
            pruned_count = 0

            for intent, pattern_list in patterns.items():
                # 1. Deduplication (removes exact duplicate step lists)
                unique_patterns = self._deduplicate(pattern_list)
                pruned_count += len(pattern_list) - len(unique_patterns)

                # -----------------------------------------------------------------
                # 🔄 NEW: Sort by efficiency (Step count metadata)
                # This ensures the shortest, most optimal solutions float to the top!
                # -----------------------------------------------------------------
                # We use a high fallback (999) if step_count is missing for some reason
                unique_patterns.sort(key=lambda x: x.get("step_count", 999))

                # 2. Cap limit: Keep only the top X best patterns per intent
                max_patterns = getattr(settings, "MAX_PATTERNS_PER_INTENT", 3)
                if len(unique_patterns) > max_patterns:
                    pruned_count += len(unique_patterns) - max_patterns
                    # Keep the ones at the beginning of the list (the shortest ones!)
                    unique_patterns = unique_patterns[:max_patterns]

                optimized_patterns[intent] = unique_patterns

            # Save the trimmed database back to disk
            with open(self.store_path, "w") as f:
                json.dump({"patterns": optimized_patterns}, f, indent=4)

            if pruned_count > 0:
                self.logger.info(
                    f"✂️ Pruning complete. Removed {pruned_count} less-optimal patterns from store."
                )

        except Exception as e:
            self.logger.error(f"Failed to prune pattern store: {e}")

    