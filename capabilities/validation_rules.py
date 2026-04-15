from __future__ import annotations
 
# Single source of truth lives in the unified validator
from tools.tool_validator import INTENT_VALIDATION  # noqa: F401
 
# Backward-compatible empty list (old code may reference this)
all_validation_rules: list = []