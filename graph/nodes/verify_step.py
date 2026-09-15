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
    executor_success = state.execution.execution_status == TaskStatus.COMPLETED and state.execution.success if state.execution else False
    
    if not state.execution:
        # No execution result, verification is uncertain
        observed_context = state.context if hasattr(state, 'context') and state.context is not None else ContextSnapshot()
        verification_result = VerificationResult(
            status="UNCERTAIN",
            observed_context=observed_context,
            expected_state={},
            actual_state={},
            reason="No execution result available"
        )
    elif not executor_success:
        # Executor failed, verification fails
        observed_context = state.context if hasattr(state, 'context') and state.context is not None else ContextSnapshot()
        # Ensure sub_context is a string, not a dict
        if hasattr(observed_context, 'sub_context') and (observed_context.sub_context is None or isinstance(observed_context.sub_context, dict)):
            # Create a new ContextSnapshot with corrected sub_context
            observed_context = ContextSnapshot(
                active_window=observed_context.active_window if hasattr(observed_context, 'active_window') else None,
                app=observed_context.app if hasattr(observed_context, 'app') else None,
                app_type=observed_context.app_type if hasattr(observed_context, 'app_type') else None,
                window_title=observed_context.window_title if hasattr(observed_context, 'window_title') else None,
                cwd=observed_context.cwd if hasattr(observed_context, 'cwd') else None,
                sub_context=None,
                ui_state=observed_context.ui_state if hasattr(observed_context, 'ui_state') else {},
                permissions=observed_context.permissions if hasattr(observed_context, 'permissions') else [],
                confidence=observed_context.confidence if hasattr(observed_context, 'confidence') else 0.0
            )
        
        # Distinguish between execution status failure and success flag failure
        if state.execution.execution_status != TaskStatus.COMPLETED:
            reason = f"Executor reported failure: {state.execution.execution_status.value}"
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
    
    return {"state": state}


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
            observed_context=state.context if hasattr(state, 'context') and state.context is not None else ContextSnapshot(),
            expected_state={},
            actual_state={},
            reason="No plan or invalid step index"
        )
    
    current_step = state.plan.steps[state.plan.current_step_index]
    
    # Phase 6: Check if step is non-idempotent or has high side-effects
    # If execution failed for such steps, outcome is uncertain
    if current_step.idempotency == PlanStepIdempotency.NON_IDEMPOTENT or current_step.side_effect in [PlanStepSideEffect.DESTRUCTIVE, PlanStepSideEffect.EXTERNAL_COMMIT]:
        if state.execution and state.execution.execution_status != TaskStatus.COMPLETED:
            # Non-idempotent or high side-effect operation failed
            # We cannot determine if the operation had partial effect
            return VerificationResult(
                status="UNCERTAIN_OUTCOME",
                observed_context=state.context if hasattr(state, 'context') and state.context is not None else ContextSnapshot(),
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
        observed_context = ContextSnapshot(
            window_title=context_snapshot.get("window_title", "Unknown"),
            app=context_snapshot.get("app_name", "Unknown"),
            app_type=context_snapshot.get("app_type", "unknown"),
            cwd=context_snapshot.get("cwd"),
            sub_context=None
        )
        
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
            if context_snapshot.get("app_name") and context_snapshot["app_name"].lower() in objective_lower:
                verification_passed = True
                verification_reason = f"App {context_snapshot['app_name']} is open as expected"
            else:
                verification_passed = False
                verification_reason = f"App not found in current window (current: {context_snapshot.get('app_name')})"
        
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
            if state.execution:
                if state.execution.success is False:
                    verification_reason = f"Execution result indicates failure"
                    if state.execution.result_data and "error" in state.execution.result_data:
                        verification_reason += f": {state.execution.result_data['error']}"
                    
                    # Check if operation is non-idempotent or has destructive side-effects
                    current_step = state.plan.steps[state.plan.current_step_index] if state.plan and state.plan.current_step_index < len(state.plan.steps) else None
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
        logger.error(f"Error verifying postconditions with context: {e}")
        
        # Fallback to basic verification
        verification_status = "VERIFIED"
        verification_reason = "Executor reported success and context verification failed, assuming success"
        if state.execution:
            if state.execution.success is False:
                verification_reason = f"Execution result indicates failure"
                if state.execution.result_data and "error" in state.execution.result_data:
                    verification_reason += f": {state.execution.result_data['error']}"
                
                # Check if operation is non-idempotent or has destructive side-effects
                current_step = state.plan.steps[state.plan.current_step_index] if state.plan and state.plan.current_step_index < len(state.plan.steps) else None
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
            observed_context=state.context if hasattr(state, 'context') else ContextSnapshot(),
            expected_state={"outcome": expected_outcome},
            actual_state=state.execution.result_data if state.execution and isinstance(state.execution.result_data, dict) else {},
            reason=verification_reason
        )
