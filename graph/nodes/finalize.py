"""
Finalize Node — Operonix Graph
─────────────────────────────

Finalize node: Produces terminal result.
Per migration plan §4.2, node 12:
"finalize — builds final.success/partial/response/error for API/Dashboard/Panel/Voice"
"""
from __future__ import annotations

import logging
from typing import Dict, Any

from migration.graph_state import OperonixState
from migration.domain_contracts import FinalResult
from graph.trace_collector import get_trace_collector
from graph.error_helpers import get_error_summary

logger = logging.getLogger("Graph.Finalize")


def finalize_node(state: OperonixState) -> Dict[str, Any]:
    """Finalize node: Produces terminal result.
    
    This node creates the FinalResult that will be returned to
    API/Dashboard/Panel/Voice interfaces.
    
    Args:
        state: Current OperonixState
        
    Returns:
        Dict with updated state including final result
    """
    logger.info(f"FINALIZE: Finalizing task {state.task.task_id}")
    
    state.add_history_event("finalize_started", {
        "task_id": state.task.task_id
    })
    
    # In Phase 1 foundation, we create a simple success result
    # Later phases will build more sophisticated final results
    
    # Get error summary from state
    error_summary = get_error_summary(state)
    
    # Determine success based on errors
    has_errors = error_summary["total_errors"] > 0
    has_critical_errors = error_summary["by_severity"].get("critical", 0) > 0
    
    final_result = FinalResult(
        success=not has_critical_errors,
        partial=has_errors and not has_critical_errors,
        response=f"Task {state.task.task_id} completed{' with warnings' if has_errors else ''}",
        error=f"Encountered {error_summary['total_errors']} errors" if has_errors else None,
        task_id=state.task.task_id,
        errors=state.errors if hasattr(state, 'errors') else []
    )
    
    state.final = final_result
    
    # Phase 9: Collect trace event for final outcome
    trace_collector = get_trace_collector()
    trace_collector.collect_final_outcome(
        task_id=state.task.task_id,
        outcome_data={
            "success": final_result.success,
            "response": final_result.response,
            "error": final_result.error,
            "partial": final_result.partial,
            "error_summary": error_summary
        }
    )
    
    # End trace
    trace_collector.end_trace(
        task_id=state.task.task_id,
        success=final_result.success,
        final_outcome=final_result.response
    )
    
    state.add_history_event("finalize_completed", {
        "task_id": state.task.task_id,
        "success": final_result.success
    })
    
    state.update_timestamp()
    
    return {"final": state.final}
