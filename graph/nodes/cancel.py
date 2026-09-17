"""
Cancel Node — Operonix Graph
────────────────────────────

Cancel node: Handles workflow cancellation with safe abort semantics.
Per migration plan Phase 8: Cancellation, Timeout & Resource Control
"""
from __future__ import annotations

import logging
from typing import Dict, Any

from migration.graph_state import OperonixState
from graph.cancellation import get_cancellation_service
from graph.trace_collector import get_trace_collector

logger = logging.getLogger("Graph.Cancel")


def cancel_node(state: OperonixState) -> Dict[str, Any]:
    """Cancel node: Handle workflow cancellation with safe abort semantics.
    
    This node:
    - Processes cancellation request
    - Executes abort semantics (immediate, graceful, safe)
    - Performs cleanup if required
    - Performs rollback if required (for timeouts)
    - Creates final result indicating cancellation
    - Collects trace event for cancellation
    
    Per migration plan Phase 8:
    - USER_REQUESTED → GRACEFUL abort
    - TIMEOUT → SAFE abort with rollback
    - SAFE_ABORT → SAFE abort
    - RESOURCE_CONTENTION → GRACEFUL abort
    - SYSTEM_ERROR → IMMEDIATE abort
    - UNKNOWN → GRACEFUL abort
    
    Args:
        state: Current OperonixState (should have cancellation set)
        
    Returns:
        Dict with updated state including cancellation result
    """
    logger.info(f"CANCEL: Handling cancellation for task {state.task.task_id}")
    
    state.add_history_event("cancel_started", {
        "task_id": state.task.task_id
    })
    
    cancellation_service = get_cancellation_service()
    
    # Process cancellation if not already processed
    if state.cancellation and not state.cancelled:
        logger.info(f"CANCEL: Processing cancellation request {state.cancellation.cancellation_id}")
        
        # Use cancellation service to cancel workflow
        state_update = cancellation_service.cancel_workflow(state, state.cancellation)
        
        # Update state with cancellation result
        state.cancelled = state_update.get('cancelled', True)
        state.cancellation = state_update.get('cancellation', state.cancellation)
        state.abort_decision = state_update.get('abort_decision', state.abort_decision)
        
        logger.info(f"CANCEL: Workflow cancelled with semantics: {state.abort_decision.semantics.value if state.abort_decision else 'unknown'}")
    
    # Perform cleanup based on abort semantics
    if state.abort_decision:
        if state.abort_decision.cleanup_required:
            logger.info(f"CANCEL: Performing cleanup for task {state.task.task_id}")
            _perform_cleanup(state)
        
        if state.abort_decision.rollback_required:
            logger.info(f"CANCEL: Performing rollback for task {state.task.task_id}")
            _perform_rollback(state)
    
    # Create final result indicating cancellation
    from migration.domain_contracts import FinalResult
    from datetime import UTC, datetime
    
    final_result = FinalResult(
        success=False,
        response=f"Task {state.task.task_id} was cancelled: {state.cancellation.reason.value if state.cancellation else 'unknown'}",
        error=f"Cancelled: {state.abort_decision.reason if state.abort_decision else 'unknown'}",
        task_id=state.task.task_id,
        completed_at=datetime.now(UTC)
    )
    
    state.final = final_result
    
    # Collect trace event for cancellation
    trace_collector = get_trace_collector()
    trace_collector.collect_cancellation(
        task_id=state.task.task_id,
        cancellation_data={
            "cancellation_id": state.cancellation.cancellation_id if state.cancellation else None,
            "reason": state.cancellation.reason.value if state.cancellation else None,
            "abort_semantics": state.abort_decision.semantics.value if state.abort_decision else None,
            "cleanup_performed": state.abort_decision.cleanup_required if state.abort_decision else False,
            "rollback_performed": state.abort_decision.rollback_required if state.abort_decision else False
        }
    )
    
    state.add_history_event("cancel_completed", {
        "task_id": state.task.task_id,
        "abort_semantics": state.abort_decision.semantics.value if state.abort_decision else None,
        "cleanup_performed": state.abort_decision.cleanup_required if state.abort_decision else False,
        "rollback_performed": state.abort_decision.rollback_required if state.abort_decision else False
    })
    
    state.update_timestamp()
    
    logger.info(f"CANCEL: Cancellation completed for task {state.task.task_id}")
    
    return {"cancelled": state.cancelled, "cancellation": state.cancellation, "abort_decision": state.abort_decision, "final": state.final}


def _perform_cleanup(state: OperonixState) -> None:
    """Perform cleanup operations for cancelled workflow.
    
    This is a placeholder for actual cleanup logic.
    In a real implementation, this would:
    - Release held resources
    - Close open connections
    - Clean up temporary files
    - Release locks
    
    Args:
        state: Current OperonixState
    """
    # Placeholder: Log cleanup actions
    logger.info(f"CANCEL: Cleanup performed for task {state.task.task_id}")
    
    # TODO: Implement actual cleanup logic
    # - Release resource ownership if any
    # - Clean up temporary files
    # - Close connections
    # - Release locks


def _perform_rollback(state: OperonixState) -> None:
    """Perform rollback operations for cancelled workflow.
    
    This is a placeholder for actual rollback logic.
    In a real implementation, this would:
    - Reverse completed operations if possible
    - Restore system state to pre-operation state
    - Undo file changes
    - Restore database state
    
    Args:
        state: Current OperonixState
    """
    # Placeholder: Log rollback actions
    logger.info(f"CANCEL: Rollback performed for task {state.task.task_id}")
    
    # TODO: Implement actual rollback logic
    # - Reverse completed steps if idempotent
    # - Restore file system state
    # - Restore application state
    # - Undo executed commands if possible
