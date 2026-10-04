"""
Context Helper Utilities — Operonix Graph
────────────────────────────────────────

Helper functions for working with ContextSnapshot objects.
Provides conversion and safe access utilities.
"""
from __future__ import annotations

from typing import Dict, Any, Optional
from migration.domain_contracts import ContextSnapshot


def context_to_dict(context: Optional[ContextSnapshot]) -> Dict[str, Any]:
    """Convert ContextSnapshot to dictionary for services that expect dict.
    
    Args:
        context: ContextSnapshot object or None
        
    Returns:
        Dictionary representation of context
    """
    if context is None:
        return {}
    
    # Convert ContextSnapshot to dict
    return {
        "window_title": context.window_title,
        "app": context.app,
        "app_name": context.app,  # Both fields for compatibility
        "app_type": context.app_type,
        "cwd": context.cwd,
        "sub_context": context.sub_context,
        "ui_state": context.ui_state if context.ui_state else {},
        "permissions": context.permissions if context.permissions else [],
        "confidence": context.confidence,
        "captured_at": context.captured_at.isoformat() if context.captured_at else None
    }


def safe_get_context_field(context: Optional[ContextSnapshot], field: str, default: Any = None) -> Any:
    """Safely get a field from ContextSnapshot with fallback.
    
    Args:
        context: ContextSnapshot object or None
        field: Field name to access
        default: Default value if field is missing
        
    Returns:
        Field value or default
    """
    if context is None:
        return default
    
    return getattr(context, field, default)


def get_validation_from_context(context: Optional[ContextSnapshot]) -> Dict[str, Any]:
    """Extract validation data from context ui_state.
    
    Args:
        context: ContextSnapshot object or None
        
    Returns:
        Validation data dict or empty dict
    """
    if context is None or context.ui_state is None:
        return {}
    
    return context.ui_state.get("validation", {})
