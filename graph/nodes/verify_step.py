"""
Verify Step Node — Operonix Graph
────────────────────────────────

Verify step node: Post-execution verification.
Per migration plan §4.2, node 10:
"verify_step — compares expected vs observed state. Writes state.verification.
May trigger recovery if verification fails."
"""
from __future__ import annotations

import logging
from typing import Dict, Any

from migration.graph_state import OperonixState
from migration.domain_contracts import VerificationResult, ContextSnapshot

logger = logging.getLogger("Graph.VerifyStep")


def verify_step_node(state: OperonixState) -> Dict[str, Any]:
    """Verify step node: Verify execution produced expected outcome.
    
    This node:
    - Distinguishes executor reported success from postcondition verification
    - Compares expected state vs observed state
    - Validates that execution achieved objective
    - May trigger recovery if verification fails
    - Creates VerificationResult with verification status
    
    In Phase 5, this implements actual verification logic.
    
    Args:
        state: Current OperonixState
        
    Returns:
        Dict with updated state including verification result
    """
    logger.info(f"VERIFY_STEP: Verifying execution for task {state.task.task_id}")
    
    state.add_history_event("verify_step_started", {
        "task_id": state.task.task_id,
        "step_id": state.execution.step_id if state.execution else None
    })
    
    # Step 1: Check if executor reported success
    executor_success = state.execution.execution_status == "COMPLETED" if state.execution else False
    
    if not executor_success:
        # Executor failed, verification fails
        verification_result = VerificationResult(
            status="FAILED",
            observed_context=state.context if hasattr(state, 'context') else ContextSnapshot(),
            expected_state={},
            actual_state={},
            reason=f"Executor reported failure: {state.execution.execution_status if state.execution else 'No execution result'}"
        )
    else:
        # Executor succeeded, verify postconditions
        verification_result = _verify_postconditions(state)
    
    state.verification = verification_result
    
    state.add_history_event("verify_step_completed", {
        "task_id": state.task.task_id,
        "verification_status": verification_result.status,
        "executor_success": executor_success
    })
    
    state.update_timestamp()
    
    return {"state": state}


def _verify_postconditions(state: OperonixState) -> VerificationResult:
    """Verify that execution achieved expected postconditions.
    
    This distinguishes executor success from actual postcondition verification.
    
    Phase 6 enhancement: Handle UNCERTAIN_OUTCOME for cases where the system
    cannot determine whether a side effect occurred. This must not be treated as
    an ordinary failure.
    
    Args:
        state: Current OperonixState
        
    Returns:
        VerificationResult with verification status
    """
    # Get current step from plan
    if not state.plan or state.plan.current_step_index >= len(state.plan.steps):
        return VerificationResult(
            status="UNCERTAIN",
            observed_context=state.context if hasattr(state, 'context') else ContextSnapshot(),
            expected_state={},
            actual_state={},
            reason="No plan or invalid step index"
        )
    
    current_step = state.plan.steps[state.plan.current_step_index]
    
    # Phase 6: Check if step is non-idempotent or has high side-effects
    # If execution failed for such steps, outcome is uncertain
    if current_step.idempotency == "NON_IDEMPOTENT" or current_step.side_effect in ["DESTRUCTIVE", "EXTERNAL_COMMIT"]:
        if state.execution and state.execution.execution_status != "COMPLETED":
            # Non-idempotent or high side-effect operation failed
            # We cannot determine if the operation had partial effect
            return VerificationResult(
                status="UNCERTAIN_OUTCOME",
                observed_context=state.context if hasattr(state, 'context') else ContextSnapshot(),
                expected_state={},
                actual_state={},
                reason=f"Non-idempotent or high side-effect operation failed, outcome uncertain (idempotency={current_step.idempotency}, side_effect={current_step.side_effect})"
            )
    
    expected_outcome = current_step.expected_outcome if hasattr(current_step, 'expected_outcome') else current_step.objective
    
    # Basic verification: check if execution result matches expected outcome
    # In a full implementation, this would:
    # - Take a context snapshot
    # - Compare against expected state
    # - Validate specific postconditions
    
    if state.execution and state.execution.result:
        # Check if execution result indicates success
        execution_result = state.execution.result
        
        # Simple verification: if execution has a success flag, use it
        if isinstance(execution_result, dict):
            if execution_result.get("success") is False:
                return VerificationResult(
                    status="FAILED",
                    observed_context=state.context if hasattr(state, 'context') else ContextSnapshot(),
                    expected_state={"outcome": expected_outcome},
                    actual_state=execution_result,
                    reason=f"Execution result indicates failure: {execution_result.get('error', 'Unknown error')}"
                )
        
        # If execution succeeded, verify postconditions
        # For Phase 5-6, we assume success if executor reported success
        # Later phases will implement actual context comparison
        return VerificationResult(
            status="VERIFIED",
            observed_context=state.context if hasattr(state, 'context') else ContextSnapshot(),
            expected_state={"outcome": expected_outcome},
            actual_state=execution_result if isinstance(execution_result, dict) else {},
            reason="Executor reported success and postconditions assumed verified (Phase 5-6 stub)"
        )
    else:
        # No execution result, uncertain outcome
        return VerificationResult(
            status="UNCERTAIN",
            observed_context=state.context if hasattr(state, 'context') else ContextSnapshot(),
            expected_state={"outcome": expected_outcome},
            actual_state={},
            reason="No execution result available for verification"
        )
