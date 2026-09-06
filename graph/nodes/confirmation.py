"""
Confirmation Node — Operonix Graph
──────────────────────────────────

Confirmation node: Handles human intervention for safety checks.
Per migration plan Phase 7: Checkpointing, Pause/Resume & Human Intervention
"""
from __future__ import annotations

import logging
from typing import Dict, Any

from migration.graph_state import OperonixState
from migration.domain_contracts import HumanIntervention, HumanInterventionType
from migration.graph_state import CheckpointState
from graph.checkpointing import get_checkpointing_service

logger = logging.getLogger("Graph.Confirmation")


def confirmation_node(state: OperonixState) -> Dict[str, Any]:
    """Confirmation node: Handle human intervention for safety checks.
    
    This node:
    - Creates human intervention request
    - Checkpoints state before pausing
    - Pauses graph execution
    - Waits for human response
    - Resumes with human decision
    
    Per migration plan Phase 7:
    ```
    SAFETY_CHECK
    ↓
    CONFIRMATION_REQUIRED
    ↓
    checkpoint
    ↓
    PAUSE
    ↓
    human response
    ↓
    RESUME
    ```
    
    Initial implementation: CONFIRM, DENY
    Future-capable: CLARIFY, CHOOSE, PROVIDE_INFORMATION, TAKE_OVER, ABORT
    
    Args:
        state: Current OperonixState
        
    Returns:
        Dict with updated state including human intervention
    """
    logger.info(f"CONFIRMATION: Handling human intervention for task {state.task.task_id}")
    
    state.add_history_event("confirmation_started", {
        "task_id": state.task.task_id
    })
    
    # Create human intervention request
    intervention = HumanIntervention(
        task_id=state.task.task_id,
        intervention_type=HumanInterventionType.CONFIRM,
        reason="Safety check requires confirmation",
        context={
            "intent": state.intent.name if state.intent else None,
            "parameters": state.intent.parameters if state.intent else {}
        }
    )
    
    state.confirmation = intervention
    
    # Create checkpoint before pausing
    checkpointing_service = get_checkpointing_service()
    checkpoint = checkpointing_service.create_checkpoint(state, "confirmation")
    
    # Store checkpoint ID in state for resume
    state.checkpoint_id = checkpoint.checkpoint_id
    
    # Mark graph as paused
    state.paused = True
    
    logger.info(f"CONFIRMATION: Checkpoint created: {checkpoint.checkpoint_id}, graph paused")
    
    state.add_history_event("confirmation_paused", {
        "task_id": state.task.task_id,
        "checkpoint_id": checkpoint.checkpoint_id,
        "intervention_type": intervention.intervention_type.value
    })
    
    state.update_timestamp()
    
    return {"state": state}


def resume_from_confirmation(state: OperonixState, human_response: HumanInterventionType) -> Dict[str, Any]:
    """Resume graph execution after human intervention.
    
    Args:
        state: Current OperonixState
        human_response: Human's response (CONFIRM, DENY, etc.)
        
    Returns:
        Dict with updated state including human response
    """
    logger.info(f"CONFIRMATION: Resuming from human intervention: {human_response.value}")
    
    # Update human intervention with response
    if state.confirmation:
        state.confirmation.response = human_response
        from datetime import datetime
        state.confirmation.responded_at = datetime.utcnow()
    
    # Mark graph as resumed
    state.paused = False
    
    state.add_history_event("confirmation_resumed", {
        "task_id": state.task.task_id,
        "human_response": human_response.value
    })
    
    state.update_timestamp()
    
    return {"state": state}
