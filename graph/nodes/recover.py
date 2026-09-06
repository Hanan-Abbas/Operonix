"""
Recover Node — Operonix Graph
─────────────────────────────

Recover node: Recovery from failures.
Per migration plan §4.2, node 11:
"recover — handles recovery from failures. Supports retry, observe, route, replan."
"""
from __future__ import annotations

import logging
from typing import Dict, Any

from migration.graph_state import OperonixState
from migration.domain_contracts import RecoveryDecision, FailureCategory, RecoveryStrategy

logger = logging.getLogger("Graph.Recover")


def recover_node(state: OperonixState) -> Dict[str, Any]:
    """Recover node: Handle recovery from failures.
    
    This node:
    - Classifies failure type
    - Determines recovery strategy (retry, observe, route, replan, abort)
    - Applies recovery mapping based on error taxonomy
    - Creates RecoveryDecision with target stage
    - Updates retry count
    
    Args:
        state: Current OperonixState
        
    Returns:
        Dict with updated state including recovery decision
    """
    logger.info(f"RECOVER: Handling recovery for task {state.task.task_id}")
    
    state.add_history_event("recover_started", {
        "task_id": state.task.task_id,
        "verification_status": state.verification.status if state.verification else None
    })
    
    # Determine failure category and recovery strategy
    failure_category = _classify_failure(state)
    recovery_strategy = _determine_recovery_strategy(failure_category, state)
    
    # Create recovery decision
    recovery_decision = RecoveryDecision(
        failure_category=failure_category,
        recovery_strategy=recovery_strategy,
        retry_count=_get_retry_count(state) + 1 if recovery_strategy == RecoveryStrategy.RETRY else _get_retry_count(state),
        target_stage=_get_target_stage(recovery_strategy, state),
        reason=_get_recovery_reason(failure_category, recovery_strategy)
    )
    
    state.recovery = recovery_decision
    
    state.add_history_event("recover_completed", {
        "task_id": state.task.task_id,
        "failure_category": failure_category.value,
        "recovery_strategy": recovery_strategy.value,
        "target_stage": recovery_decision.target_stage
    })
    
    state.update_timestamp()
    
    return {"state": state}


def _classify_failure(state: OperonixState) -> FailureCategory:
    """Classify the failure type based on verification result and execution result.
    
    Per migration plan §12.2 - Execution failure subclasses:
    - TRANSIENT
    - PERMANENT
    - ENVIRONMENTAL
    - CONTEXT_MISMATCH
    - ROUTING_MISMATCH
    - TOOL_UNAVAILABLE
    - PERMISSION_DENIED
    - VALIDATION_REJECTED
    - UNKNOWN
    
    Args:
        state: Current OperonixState
        
    Returns:
        FailureCategory classification
    """
    if not state.verification:
        return FailureCategory.UNKNOWN
    
    verification_status = state.verification.status
    
    if verification_status == "VERIFIED":
        # No failure, shouldn't be in recover node
        return FailureCategory.UNKNOWN
    
    reason = state.verification.reason or ""
    
    # Classify based on verification reason and execution result
    if "transient" in reason.lower() or "timeout" in reason.lower():
        return FailureCategory.TRANSIENT
    
    elif "context" in reason.lower() or "cwd" in reason.lower() or "window" in reason.lower():
        return FailureCategory.CONTEXT_MISMATCH
    
    elif "routing" in reason.lower() or "method" in reason.lower():
        return FailureCategory.ROUTING_MISMATCH
    
    elif "tool" in reason.lower() or "capability" in reason.lower():
        return FailureCategory.TOOL_UNAVAILABLE
    
    elif "permission" in reason.lower() or "denied" in reason.lower() or "safety" in reason.lower():
        return FailureCategory.PERMISSION_DENIED
    
    elif "validation" in reason.lower():
        return FailureCategory.VALIDATION_REJECTED
    
    elif "planning" in reason.lower():
        return FailureCategory.PLANNING_ERROR
    
    elif "environment" in reason.lower():
        return FailureCategory.ENVIRONMENTAL
    
    elif "permanent" in reason.lower():
        return FailureCategory.PERMANENT
    
    else:
        return FailureCategory.UNKNOWN


def _determine_recovery_strategy(failure_category: FailureCategory, state: OperonixState) -> RecoveryStrategy:
    """Determine recovery strategy based on failure category.
    
    Per migration plan §12.3 - Recovery mapping:
    - TRANSIENT → retry
    - CONTEXT_MISMATCH → observe
    - ROUTING_MISMATCH → re-route
    - PLANNING_ERROR → re-plan
    - PERMISSION / SAFETY → block / confirmation / finalize
    - TOOL_UNAVAILABLE → re-route
    - VERIFICATION_FAILURE → observe → recover
    - UNKNOWN / SYSTEM → controlled failure
    
    Args:
        failure_category: Classified failure type
        state: Current OperonixState
        
    Returns:
        RecoveryStrategy to apply
    """
    # Check retry count to prevent infinite retries
    retry_count = _get_retry_count(state)
    if retry_count >= 3:
        logger.warning(f"Max retries ({retry_count}) exceeded, aborting")
        return RecoveryStrategy.ABORT
    
    # Apply recovery mapping
    if failure_category == FailureCategory.TRANSIENT:
        return RecoveryStrategy.RETRY
    
    elif failure_category == FailureCategory.CONTEXT_MISMATCH:
        return RecoveryStrategy.OBSERVE
    
    elif failure_category == FailureCategory.ROUTING_MISMATCH:
        return RecoveryStrategy.ROUTE
    
    elif failure_category == FailureCategory.TOOL_UNAVAILABLE:
        return RecoveryStrategy.ROUTE
    
    elif failure_category == FailureCategory.PLANNING_ERROR:
        return RecoveryStrategy.REPLAN
    
    elif failure_category == FailureCategory.PERMISSION_DENIED:
        return RecoveryStrategy.ABORT  # Could also trigger confirmation flow
    
    elif failure_category == FailureCategory.VALIDATION_REJECTED:
        return RecoveryStrategy.ABORT
    
    elif failure_category == FailureCategory.ENVIRONMENTAL:
        return RecoveryStrategy.ABORT
    
    elif failure_category == FailureCategory.PERMANENT:
        return RecoveryStrategy.ABORT
    
    else:  # UNKNOWN
        return RecoveryStrategy.ABORT


def _get_retry_count(state: OperonixState) -> int:
    """Get current retry count from state.
    
    Args:
        state: Current OperonixState
        
    Returns:
        Current retry count
    """
    if state.recovery:
        return state.recovery.retry_count
    return 0


def _get_target_stage(recovery_strategy: RecoveryStrategy, state: OperonixState) -> str:
    """Get target stage for recovery.
    
    Args:
        recovery_strategy: Recovery strategy to apply
        state: Current OperonixState
        
    Returns:
        Target stage name for graph routing
    """
    if recovery_strategy == RecoveryStrategy.RETRY:
        return "execute_step"
    
    elif recovery_strategy == RecoveryStrategy.OBSERVE:
        return "observe"
    
    elif recovery_strategy == RecoveryStrategy.ROUTE:
        return "route"
    
    elif recovery_strategy == RecoveryStrategy.REPLAN:
        return "create_plan"
    
    elif recovery_strategy == RecoveryStrategy.ABORT:
        return "finalize"
    
    else:
        return "finalize"


def _get_recovery_reason(failure_category: FailureCategory, recovery_strategy: RecoveryStrategy) -> str:
    """Get human-readable reason for recovery decision.
    
    Args:
        failure_category: Classified failure type
        recovery_strategy: Recovery strategy to apply
        
    Returns:
        Human-readable reason string
    """
    return f"Failure classified as {failure_category.value}, applying {recovery_strategy.value} strategy"
