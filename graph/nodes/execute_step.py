"""
Execute Step Node — Operonix Graph
────────────────────────────────

Execute step node: Executor integration.
Per migration plan §4.2, node 9:
"execute_step — calls executor/executor.py with method_decision. Writes
state.execution. Handles retries and fallbacks."
"""
from __future__ import annotations

import logging
from typing import Dict, Any

from migration.graph_state import OperonixState
from migration.domain_contracts import ExecutionRequest, ExecutionResult, TaskStatus

logger = logging.getLogger("Graph.ExecuteStep")


def execute_step_node(state: OperonixState) -> Dict[str, Any]:
    """Execute step node: Execute current plan step.
    
    This node:
    - Creates ExecutionRequest with current step and routing decision
    - Calls executor to execute the step
    - Handles retries and fallbacks
    - Creates ExecutionResult with outcome
    
    In Phase 4, this is a stub that creates a placeholder ExecutionResult.
    Later phases will integrate with existing executor/ module.
    
    Args:
        state: Current OperonixState
        
    Returns:
        Dict with updated state including execution result
    """
    logger.info(f"EXECUTE_STEP: Executing step for task {state.task.task_id}")
    
    state.add_history_event("execute_step_started", {
        "task_id": state.task.task_id,
        "step_id": state.plan.current_step.step_id if state.plan and state.plan.current_step else None,
        "method": state.routing.selected_candidate.method_type if state.routing else None
    })
    
    # In Phase 4, we create a placeholder execution result
    # Later phases will integrate with:
    # - from executor.executor import Executor
    # - from executor.retry_manager import RetryManager
    # - from executor.fallback_manager import FallbackManager
    
    logger.info("EXECUTE_STEP: Executor integration deferred to later phases")
    
    # Create placeholder execution result
    execution_result = ExecutionResult(
        execution_id="placeholder_exec_id",
        step_id=state.plan.current_step.step_id if state.plan and state.plan.current_step else "unknown",
        success=True,
        method_used=state.routing.selected_candidate.method_type if state.routing else "unknown",
        execution_status=TaskStatus.COMPLETED,
        result_data={"note": "Executor integration deferred to later phases"}
    )
    
    state.execution = execution_result
    
    # Update plan progress
    if state.plan and state.plan.current_step:
        state.plan.current_step_index += 1
        if state.plan.current_step.step_id not in state.plan.completed_steps:
            state.plan.completed_steps.append(state.plan.current_step.step_id)
    
    state.add_history_event("execute_step_completed", {
        "task_id": state.task.task_id,
        "success": execution_result.success,
        "execution_status": execution_result.execution_status.value
    })
    
    state.update_timestamp()
    
    return {"state": state}
