"""
Observe Node — Operonix Graph
────────────────────────────

Observe node: Gathers context about current environment.
Per migration plan §4.2, node 2:
"gather_context / observe — calls WindowDetector, AppClassifier, StateExtractor,
 FocusTracker, ContextValidator; writes state.context"
"""
from __future__ import annotations

import logging
from typing import Dict, Any

from migration.graph_state import OperonixState

logger = logging.getLogger("Graph.Observe")


def observe_node(state: OperonixState) -> Dict[str, Any]:
    """Observe node: Gathers context about current environment.
    
    This node calls context services to understand the current state:
    - WindowDetector
    - AppClassifier
    - StateExtractor
    - FocusTracker
    - ContextValidator
    
    Phase 6 enhancement: Check postconditions to determine if operation already happened.
    This is for safe re-execution - if a non-idempotent operation failed but may have
    already succeeded, we should check before retrying.
    
    Per migration plan Phase 6:
    ```
    failure
      ↓
    observe
      ↓
    check postcondition
      ↓
    already happened?
      ├── yes → verify / continue
      └── no  → retry / recover
    ```
    
    Args:
        state: Current OperonixState
        
    Returns:
        Dict with updated state
    """
    logger.info(f"OBSERVE: Gathering context for task {state.task.task_id}")
    
    state.add_history_event("observe_started", {
        "task_id": state.task.task_id
    })
    
    # Phase 6: Check if this is a recovery observation (checking if operation already happened)
    is_recovery_observation = state.recovery is not None and state.recovery.recovery_strategy.value == "observe"
    
    if is_recovery_observation:
        # Check postconditions to determine if operation already happened
        postcondition_check = _check_postconditions(state)
        
        # Store postcondition check result in state for recovery decision
        state.context = state.context or {}
        state.context["postcondition_check"] = postcondition_check
        
        logger.info(f"OBSERVE: Postcondition check result: {postcondition_check}")
    else:
        # Initial observation (not recovery)
        logger.info("OBSERVE: Initial observation (not recovery)")
        
        # In Phase 1 foundation, we don't actually call context services
        # We just log that observation would happen
        # Later phases will integrate:
        # - from context.window_detector import WindowDetector
        # - from context.app_classifier import AppClassifier
        # - from context.state_extractor import StateExtractor
        # - from context.focus_tracker import FocusTracker
        # - from context.context_validator import ContextValidator
        
        logger.info("OBSERVE: Context services integration deferred to later phases")
    
    state.add_history_event("observe_completed", {
        "task_id": state.task.task_id,
        "is_recovery_observation": is_recovery_observation,
        "postcondition_check": state.context.get("postcondition_check") if is_recovery_observation else None
    })
    
    state.update_timestamp()
    
    return {"state": state}


def _check_postconditions(state: OperonixState) -> bool:
    """Check if postconditions are already met (operation already happened).
    
    This is used during recovery to determine if a failed operation may have
    already succeeded. If postconditions are met, we can continue instead of retrying.
    
    Args:
        state: Current OperonixState
        
    Returns:
        True if postconditions are met (operation already happened), False otherwise
    """
    # Get current step from plan
    if not state.plan or state.plan.current_step_index >= len(state.plan.steps):
        return False  # No plan or invalid step, cannot determine
    
    current_step = state.plan.steps[state.plan.current_step_index]
    
    # In Phase 6, we implement basic postcondition checking
    # Later phases will integrate with actual context observation
    
    expected_outcome = current_step.expected_outcome if hasattr(current_step, 'expected_outcome') else current_step.objective
    
    # Basic postcondition check: if we have a verification result, check its status
    if state.verification and state.verification.status == "VERIFIED":
        logger.info(f"Postconditions already verified for step {current_step.step_id}")
        return True
    
    # If verification failed or uncertain, check if we can observe the expected state
    # For Phase 6, this is a stub - later phases will implement actual context checking
    # Example: if step is "create file /tmp/test.txt", check if file exists
    
    # Placeholder: assume postconditions not met (safe default)
    # Later phases will implement actual context observation
    logger.info(f"Postcondition check for step {current_step.step_id}: cannot determine (Phase 6 stub)")
    return False
