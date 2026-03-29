"""
Load all *_ops modules into the global capability registry and attach intent-scoped validators.
Called once at agent startup (see core/main.py).
"""
import capabilities as capabilities_pkg
from capabilities.registry import capability_registry
from capabilities import validation_rules as vr


def init_capabilities() -> None:
    capability_registry.auto_register_ops(capabilities_pkg)
    for intent, rules in vr.INTENT_VALIDATION.items():
        for rule in rules:
            capability_registry.add_intent_validation(intent, rule)
