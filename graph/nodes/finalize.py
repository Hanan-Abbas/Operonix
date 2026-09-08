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
    final_result = FinalResult(
        success=True,
        response=f"Task {state.task.task_id} completed (Phase 1 foundation)",
        task_id=state.task.task_id
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
            "partial": final_result.partial
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
    
    return {"state": state}
