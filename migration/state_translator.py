"""
State Translator — Operonix Migration
────────────────────────────────────

Translation functions for converting between legacy active_tasks dict
and new OperonixState for LangGraph workflow.
"""
from __future__ import annotations

import uuid
import logging
from typing import Any, Dict, Optional

from migration.graph_state import OperonixState
from migration.domain_contracts import (
    TaskRequest, TaskSource, ContextSnapshot
)

logger = logging.getLogger("StateTranslator")


def translate_legacy_to_graph_state(legacy_task: dict) -> OperonixState:
    """Translate legacy active_tasks dict to OperonixState.
    
    Args:
        legacy_task: Legacy task dict from orchestrator.active_tasks
        
    Returns:
        OperonixState for graph execution
    """
    # Map source string to enum
    source_map = {
        "voice": TaskSource.VOICE,
        "panel": TaskSource.PANEL,
        "api": TaskSource.API,
        "cli": TaskSource.CLI,
        "unknown": TaskSource.API
    }
    
    # Create task request
    task_request = TaskRequest(
        task_id=legacy_task.get("task_id", str(uuid.uuid4())),
        user_input=legacy_task.get("input", ""),
        source=source_map.get(legacy_task.get("source", "unknown"), TaskSource.API),
        metadata={
            "legacy_task": True,
            "preferred_method": legacy_task.get("preferred_method"),
            "profile_hint": legacy_task.get("profile_hint"),
            "original_status": legacy_task.get("status"),
            "started_at": legacy_task.get("started_at")
        }
    )
    
    # Create context snapshot
    legacy_context = legacy_task.get("context", {})
    context_snapshot = ContextSnapshot(
        active_window=legacy_context.get("window_title"),
        app=legacy_context.get("app_name"),
        app_type=legacy_context.get("app_type"),
        window_title=legacy_context.get("window_title"),
        cwd=legacy_context.get("cwd"),
        sub_context=legacy_context.get("sub_context"),
        ui_state=legacy_context.get("ui_state", {}),
        permissions=legacy_context.get("permissions", []),
        confidence=legacy_context.get("confidence", 0.0)
    )
    
    # Create OperonixState
    return OperonixState(
        task=task_request,
        context=context_snapshot
    )


def translate_graph_to_legacy_state(graph_state: OperonixState) -> dict:
    """Translate OperonixState back to legacy active_tasks dict format.
    
    This is useful for fallback scenarios and state reconstruction.
    
    Args:
        graph_state: OperonixState from graph execution
        
    Returns:
        Legacy-style task dict
    """
    import time
    
    # Map TaskSource enum back to string
    source_map = {
        TaskSource.VOICE: "voice",
        TaskSource.PANEL: "panel",
        TaskSource.API: "api",
        TaskSource.CLI: "cli",
    }
    
    # Extract context from ContextSnapshot
    context_dict = {}
    if graph_state.context:
        context_dict = {
            "window_title": graph_state.context.window_title,
            "app_name": graph_state.context.app,
            "app_type": graph_state.context.app_type,
            "cwd": graph_state.context.cwd,
            "sub_context": graph_state.context.sub_context,
            "ui_state": graph_state.context.ui_state,
            "permissions": graph_state.context.permissions,
            "confidence": graph_state.context.confidence
        }
    
    # Determine legacy status from graph state
    legacy_status = _map_graph_status_to_legacy(graph_state)
    
    return {
        "task_id": graph_state.task.task_id,
        "input": graph_state.task.user_input,
        "source": source_map.get(graph_state.task.source, "unknown"),
        "status": legacy_status,
        "preferred_method": graph_state.task.metadata.get("preferred_method") if graph_state.task.metadata else None,
        "profile_hint": graph_state.task.metadata.get("profile_hint") if graph_state.task.metadata else None,
        "cwd": context_dict.get("cwd"),
        "context": context_dict,
        "started_at": graph_state.task.created_at.timestamp() if graph_state.task.created_at else time.monotonic(),
        "graph_managed": True
    }


def _map_graph_status_to_legacy(graph_state: OperonixState) -> str:
    """Map graph state status to legacy status string.
    
    Args:
        graph_state: OperonixState
        
    Returns:
        Legacy status string
    """
    # Check final result first
    if graph_state.final:
        if graph_state.final.success:
            return "completed"
        elif graph_state.final.partial:
            return "partial"
        else:
            return "failed"
    
    # Check execution status
    if graph_state.execution:
        if graph_state.execution.success:
            return "executing_success"
        else:
            return "executing_failed"
    
    # Check if paused
    if graph_state.paused:
        return "paused_confirmation"
    
    # Check if cancelled
    if graph_state.cancelled:
        return "cancelled"
    
    # Default to gathering context if no status determined
    return "gathering_context"


def compare_states(legacy_task: dict, graph_state: OperonixState) -> dict:
    """Compare legacy and graph states for validation.
    
    This is used in shadow mode to ensure both systems produce equivalent results.
    
    Args:
        legacy_task: Legacy task dict
        graph_state: OperonixState
        
    Returns:
        Dict with comparison results
    """
    comparison = {
        "task_id": legacy_task.get("task_id") or graph_state.task.task_id,
        "input_match": legacy_task.get("input") == graph_state.task.user_input,
        "source_match": _compare_source(legacy_task.get("source"), graph_state.task.source),
        "context_match": _compare_context(legacy_task.get("context", {}), graph_state.context),
        "status_consistent": _compare_status_consistency(legacy_task, graph_state),
        "discrepancies": []
    }
    
    # Collect discrepancies
    if not comparison["input_match"]:
        comparison["discrepancies"].append("input_mismatch")
    
    if not comparison["source_match"]:
        comparison["discrepancies"].append("source_mismatch")
    
    if not comparison["context_match"]:
        comparison["discrepancies"].append("context_mismatch")
    
    if not comparison["status_consistent"]:
        comparison["discrepancies"].append("status_inconsistency")
    
    return comparison


def _compare_source(legacy_source: str, graph_source: TaskSource) -> bool:
    """Compare legacy source string with graph TaskSource enum."""
    source_map = {
        "voice": TaskSource.VOICE,
        "panel": TaskSource.PANEL,
        "api": TaskSource.API,
        "cli": TaskSource.CLI,
    }
    return source_map.get(legacy_source) == graph_source


def _compare_context(legacy_context: dict, graph_context: Optional[ContextSnapshot]) -> bool:
    """Compare legacy context dict with graph ContextSnapshot."""
    if not graph_context:
        return not legacy_context  # Both empty
    
    # Compare key fields
    key_fields = ["window_title", "app_name", "cwd"]
    
    for field in key_fields:
        legacy_value = legacy_context.get(field)
        graph_value = getattr(graph_context, field, None)
        
        if legacy_value != graph_value:
            return False
    
    return True


def _compare_status_consistency(legacy_task: dict, graph_state: OperonixState) -> bool:
    """Check if legacy and graph statuses are logically consistent."""
    legacy_status = legacy_task.get("status")
    graph_status = _map_graph_status_to_legacy(graph_state)
    
    # Allow some status differences during transition
    compatible_statuses = {
        ("gathering_context", "gathering_context"),
        ("intent_parsing", "gathering_context"),  # Graph may not distinguish
        ("planning", "gathering_context"),  # Graph may not distinguish
        ("completed", "completed"),
        ("failed", "failed"),
        ("partial", "partial")
    }
    
    return (legacy_status, graph_status) in compatible_statuses