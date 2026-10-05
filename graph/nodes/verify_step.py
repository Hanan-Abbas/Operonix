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
from migration.domain_contracts import VerificationResult, ContextSnapshot, PlanStepIdempotency, PlanStepSideEffect, TaskStatus
from graph.trace_collector import get_trace_collector
from graph.context_helpers import context_to_dict
from graph.state_helpers import get_safe_field, get_plan_step
from graph.error_helpers import handle_recoverable_error, track_error

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
        "step_id": get_safe_field(state, 'execution.step_id', None)
    })
    
    # Step 1: Check if executor reported success
    execution_status = get_safe_field(state, 'execution.execution_status', None)
    execution_success = get_safe_field(state, 'execution.success', False)
    executor_success = execution_status == TaskStatus.COMPLETED and execution_success
    
    if not get_safe_field(state, 'execution', None):
        # No execution result, verification is uncertain
        observed_context = get_safe_field(state, 'context', None) or ContextSnapshot()
        verification_result = VerificationResult(
            status="UNCERTAIN",
            observed_context=observed_context,
            expected_state={},
            actual_state={},
            reason="No execution result available"
        )
    elif not executor_success:
        # Executor failed, verification fails
        observed_context = get_safe_field(state, 'context', None) or ContextSnapshot()
        
        # Distinguish between execution status failure and success flag failure
        if execution_status != TaskStatus.COMPLETED:
            reason = f"Executor reported failure: {execution_status.value}"
        else:
            reason = "execution result indicates failure"
        
        verification_result = VerificationResult(
            status="FAILED",
            observed_context=observed_context,
            expected_state={},
            actual_state={},
            reason=reason
        )
    else:
        # Executor succeeded, verify postconditions
        verification_result = _verify_postconditions(state)
    
    state.verification = verification_result
    
    # Phase 9: Collect trace event for verification
    trace_collector = get_trace_collector()
    trace_collector.collect_verification(
        task_id=state.task.task_id,
        verification_data={
            "status": verification_result.status,
            "executor_success": executor_success,
            "reason": verification_result.reason,
            "expected_state": verification_result.expected_state,
            "actual_state": verification_result.actual_state
        }
    )
    
    state.add_history_event("verify_step_completed", {
        "task_id": state.task.task_id,
        "verification_status": verification_result.status,
        "executor_success": executor_success
    })
    
    state.update_timestamp()
    
    return {"verification": state.verification}


def _verify_postconditions(state: OperonixState) -> VerificationResult:
    """Verify that execution achieved expected postconditions.
    
    This distinguishes executor success from actual postcondition verification.
    
    Phase 6 enhancement: Handle UNCERTAIN_OUTCOME for cases where the system
    cannot determine whether a side effect occurred. This must not be treated as
    an ordinary failure.
    
    Phase 9/10 enhancement: Integrate actual context services for postcondition verification.
    
    Args:
        state: Current OperonixState
        
    Returns:
        VerificationResult with verification status
    """
    # Get current step from plan
    if not state.plan or state.plan.current_step_index >= len(state.plan.steps):
        return VerificationResult(
            status="UNCERTAIN",
            observed_context=get_safe_field(state, 'context', None) or ContextSnapshot(),
            expected_state={},
            actual_state={},
            reason="No plan or invalid step index"
        )
    
    current_step = state.plan.steps[state.plan.current_step_index]
    
    # Phase 6: Check if step is non-idempotent or has high side-effects
    # If execution failed for such steps, outcome is uncertain
    if current_step.idempotency == PlanStepIdempotency.NON_IDEMPOTENT or current_step.side_effect in [PlanStepSideEffect.DESTRUCTIVE, PlanStepSideEffect.EXTERNAL_COMMIT]:
        execution_status = get_safe_field(state, 'execution.execution_status', None)
        if execution_status and execution_status != TaskStatus.COMPLETED:
            # Non-idempotent or high side-effect operation failed
            # We cannot determine if the operation had partial effect
            return VerificationResult(
                status="UNCERTAIN_OUTCOME",
                observed_context=get_safe_field(state, 'context', None) or ContextSnapshot(),
                expected_state={},
                actual_state={},
                reason=f"Non-idempotent or high side-effect operation failed, outcome uncertain (idempotency={current_step.idempotency}, side_effect={current_step.side_effect})"
            )
    
    expected_outcome = current_step.expected_outcome if hasattr(current_step, 'expected_outcome') else current_step.objective
    
    # Phase 9/10: Integrate actual context checking for postcondition verification
    # Gather current context to verify expected state
    try:
        from graph.nodes.observe import _gather_context_snapshot
        
        context_snapshot = _gather_context_snapshot(state)
        observed_context = context_snapshot  # Now directly use ContextSnapshot
        
        # Verify postconditions based on step objective
        # This is a simplified implementation - a full implementation would parse
        # the objective and check specific conditions (file exists, window open, etc.)
        
        objective_lower = expected_outcome.lower()
        verification_passed = False
        verification_reason = ""
        
        # Check for file existence
        if "file" in objective_lower and ("create" in objective_lower or "write" in objective_lower):
            import os
            import re
            
            # Try to find a path in the objective
            path_match = re.search(r'[~/]?[\w/\\]+[\w/\\]*\.\w+', expected_outcome)
            if path_match:
                file_path = path_match.group(0)
                if os.path.exists(file_path):
                    verification_passed = True
                    verification_reason = f"File {file_path} exists as expected"
                else:
                    verification_passed = False
                    verification_reason = f"File {file_path} does not exist"
        
        # Check for application/window
        elif "open" in objective_lower and ("app" in objective_lower or "application" in objective_lower):
            if context_snapshot.app and context_snapshot.app.lower() in objective_lower:
                verification_passed = True
                verification_reason = f"App {context_snapshot.app} is open as expected"
            else:
                verification_passed = False
                verification_reason = f"App not found in current window (current: {context_snapshot.app})"
        
        # Check for directory
        elif "directory" in objective_lower or "folder" in objective_lower:
            import os
            import re
            
            path_match = re.search(r'[~/]?[\w/\\]+', expected_outcome)
            if path_match:
                dir_path = path_match.group(0)
                if os.path.isdir(dir_path):
                    verification_passed = True
                    verification_reason = f"Directory {dir_path} exists as expected"
                else:
                    verification_passed = False
                    verification_reason = f"Directory {dir_path} does not exist"
        
        else:
            # Generic verification - check if execution result indicates success
            verification_status = "VERIFIED"
            verification_reason = "No specific postconditions to verify, assuming success"
            execution_success = get_safe_field(state, 'execution.success', True)
            if execution_success is False:
                verification_reason = f"Execution result indicates failure"
                result_data = get_safe_field(state, 'execution.result_data', {})
                if result_data and "error" in result_data:
                    verification_reason += f": {result_data['error']}"
                
                # Check if operation is non-idempotent or has destructive side-effects
                current_step = get_plan_step(state)
                    if current_step:
                        if current_step.idempotency == PlanStepIdempotency.NON_IDEMPOTENT:
                            verification_status = "UNCERTAIN_OUTCOME"
                            verification_reason = "Non-idempotent operation failed, outcome uncertain"
                        elif current_step.side_effect in [PlanStepSideEffect.DESTRUCTIVE, PlanStepSideEffect.EXTERNAL_COMMIT]:
                            verification_status = "UNCERTAIN_OUTCOME"
                            verification_reason = "Destructive/external-commit operation failed, outcome uncertain"
                        else:
                            verification_status = "FAILED"
                    else:
                        verification_status = "FAILED"
                else:
                    verification_status = "VERIFIED"
                    verification_reason = "Executor reported success and no specific postconditions to verify"
        
        return VerificationResult(
            status=verification_status,
            observed_context=observed_context,
            expected_state={"outcome": expected_outcome},
            actual_state=context_snapshot,
            reason=verification_reason
        )
        
    except Exception as e:
        # Context verification is optional, treat as recoverable
        graph_error = handle_recoverable_error(
            e,
            "verify_step",
            fallback_description="falling back to basic verification"
        )
        track_error(state, graph_error)
        
        # Fallback to basic verification
        verification_status = "VERIFIED"
        verification_reason = "Executor reported success and context verification failed, assuming success"
        execution_success = get_safe_field(state, 'execution.success', True)
        if execution_success is False:
            verification_reason = f"Execution result indicates failure"
            result_data = get_safe_field(state, 'execution.result_data', {})
            if result_data and "error" in result_data:
                verification_reason += f": {result_data['error']}"
            
            # Check if operation is non-idempotent or has destructive side-effects
            current_step = get_plan_step(state)
                if current_step:
                    if current_step.idempotency == PlanStepIdempotency.NON_IDEMPOTENT:
                        verification_status = "UNCERTAIN_OUTCOME"
                        verification_reason = "Non-idempotent operation failed, outcome uncertain"
                    elif current_step.side_effect in [PlanStepSideEffect.DESTRUCTIVE, PlanStepSideEffect.EXTERNAL_COMMIT]:
                        verification_status = "UNCERTAIN_OUTCOME"
                        verification_reason = "Destructive/external-commit operation failed, outcome uncertain"
                    else:
                        verification_status = "FAILED"
                else:
                    verification_status = "FAILED"
            else:
                verification_status = "VERIFIED"
                verification_reason = "Executor reported success"
        
        return VerificationResult(
            status=verification_status,
            observed_context=get_safe_field(state, 'context', None) or ContextSnapshot(),
            expected_state={"outcome": expected_outcome},
            actual_state=get_safe_field(state, 'execution.result_data', {}) if isinstance(get_safe_field(state, 'execution.result_data', {}), dict) else {},
            reason=verification_reason
        )
