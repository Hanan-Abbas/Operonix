"""
State Helper Utilities — Operonix Graph
──────────────────────────────────

Helper functions for safe access to OperonixState fields.
Provides consistent patterns for accessing optional state fields with defaults.
"""
from __future__ import annotations

import logging
from typing import Any, Optional, TypeVar
from migration.graph_state import OperonixState

logger = logging.getLogger("Graph.StateHelpers")


T = TypeVar('T')


def get_safe_field(state: OperonixState, field_path: str, default: Any = None) -> Any:
    """Safely get a nested field from OperonixState with default.
    
    Args:
        state: OperonixState instance
        field_path: Dot-separated path to field (e.g., 'intent.name', 'plan.current_step.step_id')
        default: Default value if field is missing
        
    Returns:
        Field value or default
        
    Examples:
        >>> intent_name = get_safe_field(state, 'intent.name', 'unknown')
        >>> step_id = get_safe_field(state, 'plan.current_step.step_id', None)
    """
    # Split the path and traverse
    parts = field_path.split('.')
    current = state
    
    for i, part in enumerate(parts):
        if current is None:
            logger.debug(f"State field missing at {'.'.join(parts[:i+1])}, using default: {default}")
            return default
        
        # Handle both attribute access and dict access
        if hasattr(current, part):
            current = getattr(current, part)
        elif isinstance(current, dict) and part in current:
            current = current[part]
        else:
            logger.debug(f"State field missing at {'.'.join(parts[:i+1])}, using default: {default}")
            return default
    
    return current if current is not None else default


def require_field(state: OperonixState, field_path: str, node_name: str) -> Any:
    """Require a field to be present in state for node execution.
    
    Args:
        state: OperonixState instance
        field_path: Dot-separated path to field (e.g., 'intent', 'plan')
        node_name: Name of the node requiring this field (for error message)
        
    Returns:
        Field value
        
    Raises:
        ValueError: If field is missing
        
    Examples:
        >>> intent = require_field(state, 'intent', 'analyze_intent')
        >>> plan = require_field(state, 'plan', 'create_plan')
    """
    value = get_safe_field(state, field_path, None)
    
    if value is None:
        raise ValueError(
            f"Node '{node_name}' requires field '{field_path}' to be populated, "
            f"but it is None or missing. State flow may be incorrect."
        )
    
    return value


def validate_state_for_node(state: OperonixState, node_name: str, required_fields: list[str]) -> bool:
    """Validate that required fields are present for a node.
    
    Args:
        state: OperonixState instance
        node_name: Name of the node being validated
        required_fields: List of required field paths (e.g., ['task', 'intent'])
        
    Returns:
        True if all required fields are present
        
    Raises:
        ValueError: If any required field is missing
    """
    missing_fields = []
    
    for field_path in required_fields:
        value = get_safe_field(state, field_path, None)
        if value is None:
            missing_fields.append(field_path)
    
    if missing_fields:
        raise ValueError(
            f"Node '{node_name}' requires fields: {required_fields}. "
            f"Missing fields: {missing_fields}. State flow may be incorrect."
        )
    
    return True


def get_nested_attr(obj: Any, attr_path: str, default: Any = None) -> Any:
    """Get a nested attribute from an object.
    
    This is a general-purpose helper for accessing nested attributes.
    
    Args:
        obj: Object to traverse
        attr_path: Dot-separated path to attribute
        default: Default value if attribute is missing
        
    Returns:
        Attribute value or default
    """
    parts = attr_path.split('.')
    current = obj
    
    for part in parts:
        if current is None:
            return default
        
        if hasattr(current, part):
            current = getattr(current, part)
        elif isinstance(current, dict) and part in current:
            current = current[part]
        else:
            return default
    
    return current if current is not None else default


def get_plan_step(state: OperonixState) -> Optional[Any]:
    """Get the current plan step with safe access.
    
    Args:
        state: OperonixState instance
        
    Returns:
        Current plan step or None
    """
    return get_safe_field(state, 'plan.current_step', None)


def get_intent_name(state: OperonixState, default: str = 'unknown') -> str:
    """Get the intent name with safe access.
    
    Args:
        state: OperonixState instance
        default: Default intent name if not present
        
    Returns:
        Intent name or default
    """
    return get_safe_field(state, 'intent.name', default)


def get_routing_method(state: OperonixState, default: str = 'unknown') -> str:
    """Get the routing method type with safe access.
    
    Args:
        state: OperonixState instance
        default: Default method type if not present
        
    Returns:
        Method type or default
    """
    return get_safe_field(state, 'routing.selected_candidate.method_type', default)


def get_execution_success(state: OperonixState) -> bool:
    """Get whether execution succeeded with safe access.
    
    Args:
        state: OperonixState instance
        
    Returns:
        True if execution succeeded, False otherwise
    """
    execution_success = get_safe_field(state, 'execution.success', False)
    execution_status = get_safe_field(state, 'execution.execution_status.value', None)
    
    # Execution succeeded if both success flag and status indicate success
    if execution_status == 'COMPLETED':
        return execution_success
    return False


def is_field_populated(state: OperonixState, field_path: str) -> bool:
    """Check if a field is populated (not None or empty dict/list).
    
    Args:
        state: OperonixState instance
        field_path: Dot-separated path to field
        
    Returns:
        True if field is populated, False otherwise
    """
    value = get_safe_field(state, field_path, None)
    
    if value is None:
        return False
    
    # Check for empty collections
    if isinstance(value, (dict, list)) and len(value) == 0:
        return False
    
    return True
