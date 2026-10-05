"""
Error Helper Utilities — Operonix Graph
────────────────────────────────────

Helper functions for error classification, handling, and tracking.
Provides consistent error handling patterns across graph nodes.
"""
from __future__ import annotations

import logging
import traceback
from enum import Enum
from typing import Optional, Dict, Any, List
from datetime import datetime, timezone

# Compatibility for Python < 3.11
try:
    from datetime import UTC
except ImportError:
    UTC = timezone.utc

logger = logging.getLogger("Graph.ErrorHelpers")


class ErrorSeverity(str, Enum):
    """Severity levels for errors."""
    RECOVERABLE = "recoverable"  # Can use fallback
    NON_RECOVERABLE = "non_recoverable"  # Should propagate
    CRITICAL = "critical"  # Should fail fast


class ErrorCategory(str, Enum):
    """Categories of errors for better tracking."""
    NETWORK = "network"
    VALIDATION = "validation"
    SERVICE_UNAVAILABLE = "service_unavailable"
    DATA_CORRUPTION = "data_corruption"
    LOGIC_ERROR = "logic_error"
    TIMEOUT = "timeout"
    IMPORT_ERROR = "import_error"
    UNKNOWN = "unknown"


class GraphError:
    """Structured error information for tracking.
    
    Captures error context including type, message, severity, category,
    stack trace, and node information.
    """
    def __init__(
        self,
        error_type: str,
        message: str,
        severity: ErrorSeverity,
        category: ErrorCategory,
        node: str,
        stack_trace: Optional[str] = None,
        context: Optional[Dict[str, Any]] = None
    ):
        self.error_type = error_type
        self.message = message
        self.severity = severity
        self.category = category
        self.node = node
        self.stack_trace = stack_trace or traceback.format_exc()
        self.context = context or {}
        self.timestamp = datetime.now(UTC).isoformat()
    
    def to_dict(self) -> Dict[str, Any]:
        """Convert to dictionary for serialization."""
        return {
            "error_type": self.error_type,
            "message": self.message,
            "severity": self.severity.value,
            "category": self.category.value,
            "node": self.node,
            "stack_trace": self.stack_trace,
            "context": self.context,
            "timestamp": self.timestamp
        }


def classify_error(
    error: Exception,
    node: str,
    context: Optional[Dict[str, Any]] = None
) -> GraphError:
    """Classify an error by type and determine severity.
    
    Args:
        error: The exception to classify
        node: Name of the node where error occurred
        context: Additional context about the error
        
    Returns:
        GraphError with classification
    """
    error_type = type(error).__name__
    error_message = str(error)
    
    # ImportError - service unavailable
    if isinstance(error, ImportError):
        return GraphError(
            error_type=error_type,
            message=error_message,
            severity=ErrorSeverity.RECOVERABLE,
            category=ErrorCategory.IMPORT_ERROR,
            node=node,
            context=context
        )
    
    # Timeout errors
    if isinstance(error, TimeoutError):
        return GraphError(
            error_type=error_type,
            message=error_message,
            severity=ErrorSeverity.RECOVERABLE,
            category=ErrorCategory.TIMEOUT,
            node=node,
            context=context
        )
    
    # Connection/Network errors
    if "Connection" in error_type or "Network" in error_type or "HTTP" in error_type:
        return GraphError(
            error_type=error_type,
            message=error_message,
            severity=ErrorSeverity.RECOVERABLE,
            category=ErrorCategory.NETWORK,
            node=node,
            context=context
        )
    
    # Validation errors
    if "Validation" in error_type or "ValueError" in error_type or "ValidationError" in error_type:
        return GraphError(
            error_type=error_type,
            message=error_message,
            severity=ErrorSeverity.NON_RECOVERABLE,
            category=ErrorCategory.VALIDATION,
            node=node,
            context=context
        )
    
    # Data corruption/missing data
    if "KeyError" in error_type or "AttributeError" in error_type:
        return GraphError(
            error_type=error_type,
            message=error_message,
            severity=ErrorSeverity.NON_RECOVERABLE,
            category=ErrorCategory.DATA_CORRUPTION,
            node=node,
            context=context
        )
    
    # Default to non-recoverable logic error
    return GraphError(
        error_type=error_type,
        message=error_message,
        severity=ErrorSeverity.NON_RECOVERABLE,
        category=ErrorCategory.LOGIC_ERROR,
        node=node,
        context=context
    )


def handle_recoverable_error(
    error: Exception,
    node: str,
    fallback_description: str,
    context: Optional[Dict[str, Any]] = None
) -> GraphError:
    """Handle a recoverable error by logging and tracking.
    
    Args:
        error: The exception that occurred
        node: Name of the node where error occurred
        fallback_description: Description of fallback being used
        context: Additional context about the error
        
    Returns:
        GraphError for tracking
    """
    graph_error = classify_error(error, node, context)
    
    logger.warning(
        f"{node}: Recoverable error - {fallback_description}. "
        f"Error: {graph_error.error_type}: {graph_error.message}"
    )
    
    return graph_error


def handle_non_recoverable_error(
    error: Exception,
    node: str,
    context: Optional[Dict[str, Any]] = None
) -> GraphError:
    """Handle a non-recoverable error by logging and raising.
    
    Args:
        error: The exception that occurred
        node: Name of the node where error occurred
        context: Additional context about the error
        
    Returns:
        GraphError for tracking
        
    Raises:
        The original error (re-raised with context)
    """
    graph_error = classify_error(error, node, context)
    
    logger.error(
        f"{node}: Non-recoverable error - {graph_error.error_type}: {graph_error.message}. "
        f"Category: {graph_error.category.value}"
    )
    
    return graph_error


def handle_critical_error(
    error: Exception,
    node: str,
    context: Optional[Dict[str, Any]] = None
) -> GraphError:
    """Handle a critical error by logging and failing fast.
    
    Args:
        error: The exception that occurred
        node: Name of the node where error occurred
        context: Additional context about the error
        
    Returns:
        GraphError for tracking
        
    Raises:
        RuntimeError with context
    """
    graph_error = GraphError(
        error_type=type(error).__name__,
        message=str(error),
        severity=ErrorSeverity.CRITICAL,
        category=ErrorCategory.UNKNOWN,
        node=node,
        context=context
    )
    
    logger.critical(
        f"{node}: Critical error - {graph_error.error_type}: {graph_error.message}. "
        f"Aborting execution."
    )
    
    raise RuntimeError(
        f"Critical error in {node}: {graph_error.message}"
    ) from error


def track_error(state: Any, error: GraphError) -> None:
    """Track an error in the state.
    
    Args:
        state: OperonixState instance
        error: GraphError to track
    """
    if not hasattr(state, 'errors') or state.errors is None:
        state.errors = []
    
    state.errors.append(error.to_dict())
    
    # Keep only last 100 errors to avoid memory issues
    if len(state.errors) > 100:
        state.errors = state.errors[-100:]


def get_error_summary(state: Any) -> Dict[str, Any]:
    """Get a summary of errors from state.
    
    Args:
        state: OperonixState instance
        
    Returns:
        Dictionary with error summary
    """
    if not hasattr(state, 'errors') or not state.errors:
        return {
            "total_errors": 0,
            "by_severity": {},
            "by_category": {},
            "by_node": {}
        }
    
    summary = {
        "total_errors": len(state.errors),
        "by_severity": {},
        "by_category": {},
        "by_node": {}
    }
    
    for error in state.errors:
        # Count by severity
        severity = error.get("severity", "unknown")
        summary["by_severity"][severity] = summary["by_severity"].get(severity, 0) + 1
        
        # Count by category
        category = error.get("category", "unknown")
        summary["by_category"][category] = summary["by_category"].get(category, 0) + 1
        
        # Count by node
        node = error.get("node", "unknown")
        summary["by_node"][node] = summary["by_node"].get(node, 0) + 1
    
    return summary


def has_critical_errors(state: Any) -> bool:
    """Check if state has any critical errors.
    
    Args:
        state: OperonixState instance
        
    Returns:
        True if critical errors present
    """
    if not hasattr(state, 'errors') or not state.errors:
        return False
    
    for error in state.errors:
        if error.get("severity") == ErrorSeverity.CRITICAL.value:
            return True
    
    return False


def get_recent_errors(state: Any, count: int = 5) -> List[Dict[str, Any]]:
    """Get the most recent errors from state.
    
    Args:
        state: OperonixState instance
        count: Number of recent errors to return
        
    Returns:
        List of recent errors
    """
    if not hasattr(state, 'errors') or not state.errors:
        return []
    
    return state.errors[-count:]
