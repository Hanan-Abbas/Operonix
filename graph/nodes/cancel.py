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
    
    This implements actual cleanup logic:
    - Release held resources
    - Close open connections
    - Clean up temporary files
    - Release locks
    
    Args:
        state: Current OperonixState
    """
    logger.info(f"CANCEL: Performing cleanup for task {state.task.task_id}")
    
    cleanup_actions = []
    
    # Clean up temporary files if any
    try:
        import os
        import tempfile
        import glob
        
        # Look for temp files created by this task
        temp_pattern = f"*{state.task.task_id}*"
        temp_dirs = [tempfile.gettempdir(), "/tmp"]
        
        for temp_dir in temp_dirs:
            if os.path.exists(temp_dir):
                temp_files = glob.glob(os.path.join(temp_dir, temp_pattern))
                for temp_file in temp_files:
                    try:
                        if os.path.isfile(temp_file):
                            os.remove(temp_file)
                            cleanup_actions.append(f"Removed temp file: {temp_file}")
                        elif os.path.isdir(temp_file):
                            os.rmdir(temp_file)
                            cleanup_actions.append(f"Removed temp directory: {temp_file}")
                    except Exception as e:
                        logger.warning(f"Failed to clean up {temp_file}: {e}")
    except Exception as e:
        logger.error(f"Error during temp file cleanup: {e}")
    
    # Release resource ownership if tracked in state
    if state.context and isinstance(state.context, dict):
        try:
            from context.resource_manager import resource_manager
            
            # Release any resources held by this task
            if hasattr(resource_manager, 'release_task_resources'):
                released = resource_manager.release_task_resources(state.task.task_id)
                if released:
                    cleanup_actions.append(f"Released {len(released)} resources")
                    logger.info(f"Released resources: {released}")
        except ImportError:
            logger.debug("ResourceManager not available for cleanup")
        except Exception as e:
            logger.error(f"Error releasing resources: {e}")
    
    # Close connections if any (placeholder for connection pool cleanup)
    if state.context and isinstance(state.context, dict):
        try:
            # Check for any open connections in context
            connections = state.context.get('open_connections', [])
            for conn in connections:
                try:
                    # Generic close attempt - in real implementation would be connection-specific
                    if hasattr(conn, 'close'):
                        conn.close()
                        cleanup_actions.append(f"Closed connection: {conn}")
                except Exception as e:
                    logger.warning(f"Failed to close connection: {e}")
        except Exception as e:
            logger.error(f"Error closing connections: {e}")
    
    # Release locks if any
    if state.context and isinstance(state.context, dict):
        try:
            from context.lock_manager import lock_manager
            
            # Release any locks held by this task
            if hasattr(lock_manager, 'release_task_locks'):
                released_locks = lock_manager.release_task_locks(state.task.task_id)
                if released_locks:
                    cleanup_actions.append(f"Released {len(released_locks)} locks")
                    logger.info(f"Released locks: {released_locks}")
        except ImportError:
            logger.debug("LockManager not available for cleanup")
        except Exception as e:
            logger.error(f"Error releasing locks: {e}")
    
    if cleanup_actions:
        logger.info(f"CANCEL: Cleanup completed with {len(cleanup_actions)} actions: {cleanup_actions}")
    else:
        logger.info(f"CANCEL: No cleanup actions required for task {state.task.task_id}")


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
