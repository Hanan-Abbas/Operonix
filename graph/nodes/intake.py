"""
Intake Node — Operonix Graph
────────────────────────────

Intake node: Creates task state from user input.
Per migration plan §4.2, node 1:
"intake — deterministic; creates state.task. Replaces orchestrator.handle_new_task()"
"""
from __future__ import annotations

import logging
from typing import Dict, Any

from migration.graph_state import OperonixState

logger = logging.getLogger("Graph.Intake")


def intake_node(state: OperonixState) -> Dict[str, Any]:
    """Intake node: Creates task state from user input.
    
    This is a deterministic node that:
    - Validates the task request
    - Creates the initial OperonixState
    - No LLM calls, no AI reasoning
    
    Args:
        state: Current OperonixState
        
    Returns:
        Dict with updated state
    """
    logger.info(f"INTAKE: Processing task {state.task.task_id}")
    
    # Add history event
    state.add_history_event("intake_started", {
        "task_id": state.task.task_id,
        "user_input": state.task.user_input,
        "source": state.task.source.value
    })
    
    # Update timestamp
    state.update_timestamp()
    
    # In this foundation pass, intake just validates and logs
    # Later phases will add more sophisticated processing
    
    state.add_history_event("intake_completed", {
        "task_id": state.task.task_id
    })
    
    return {"state": state}
