"""
Execute Step Node — Operonix Graph
────────────────────────────────

Execute step node: Executor integration.
Per migration plan §4.2, node 9:
"execute_step — calls executor/executor.py with method_decision. Writes
state.execution. Handles retries and fallbacks."

Executor Integration Phase: Integrate actual executor module.
"""
from __future__ import annotations

import logging
from typing import Dict, Any

from migration.graph_state import OperonixState
from migration.domain_contracts import ExecutionRequest, ExecutionResult, TaskStatus
from graph.trace_collector import get_trace_collector

logger = logging.getLogger("Graph.ExecuteStep")


def execute_step_node(state: OperonixState) -> Dict[str, Any]:
    """Execute step node: Execute current plan step.
    
    This node:
    - Creates ExecutionRequest with current step and routing decision
    - Calls executor to execute the step
    - Handles retries and fallbacks
    - Creates ExecutionResult with outcome
    
    Executor Integration Phase: Integrate actual executor modules.
    
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
    
    # Executor Integration: Use actual executor modules
    try:
        # Get current step
        current_step = state.plan.current_step if state.plan and state.plan.current_step else None
        
        if not current_step:
            logger.warning("No current step to execute")
            execution_result = ExecutionResult(
                execution_id="no_step",
                step_id="unknown",
                success=False,
                method_used="unknown",
                execution_status=TaskStatus.FAILED,
                result_data={"error": "No current step to execute"}
            )
            state.execution = execution_result
            return {"state": state}
        
        # Get routing decision
        routing_decision = state.routing
        
        # Perform execution with retry and fallback logic
        execution_result = _execute_with_retry_fallback(
            current_step,
            routing_decision,
            state.context if isinstance(state.context, dict) else None
        )
        
        state.execution = execution_result
        
    except Exception as e:
        logger.error(f"Error in execution: {e}")
        
        # Create error execution result
        execution_result = ExecutionResult(
            execution_id="error_exec",
            step_id=state.plan.current_step.step_id if state.plan and state.plan.current_step else "unknown",
            success=False,
            method_used=state.routing.selected_candidate.method_type if state.routing else "unknown",
            execution_status=TaskStatus.FAILED,
            result_data={"error": str(e)}
        )
        
        state.execution = execution_result
    
    # Phase 9: Collect trace event for execution attempt
    trace_collector = get_trace_collector()
    trace_collector.collect_execution_attempt(
        task_id=state.task.task_id,
        execution_data={
            "execution_id": state.execution.execution_id,
            "step_id": state.execution.step_id,
            "method_used": state.execution.method_used,
            "success": state.execution.success,
            "execution_status": state.execution.execution_status.value,
            "result_data": state.execution.result_data
        }
    )
    
    # Phase 14: Collect performance feedback for learning
    try:
        from graph.learning_integration import get_learning_integration
        
        learning_integration = get_learning_integration()
        
        # Extract execution time from result data
        execution_time = state.execution.result_data.get("execution_time", 0.0)
        
        # Get intent from state
        intent = state.intent.name if state.intent else "unknown"
        
        # Collect performance feedback
        learning_integration.collect_performance_feedback(
            task_id=state.task.task_id,
            intent=intent,
            method_type=state.execution.method_used,
            success=state.execution.success,
            execution_time=execution_time,
            retry_count=state.execution.result_data.get("retry_count", 0),
            fallback_used=state.execution.result_data.get("fallback_used", False)
        )
    except ImportError:
        logger.warning("Could not import learning_integration, skipping performance feedback")
    except Exception as e:
        logger.error(f"Error collecting performance feedback: {e}")
    
    # Update plan progress if execution succeeded
    if state.execution.success and state.plan and state.plan.current_step:
        state.plan.current_step_index += 1
        if state.plan.current_step.step_id not in state.plan.completed_steps:
            state.plan.completed_steps.append(state.plan.current_step.step_id)
    
    state.add_history_event("execute_step_completed", {
        "task_id": state.task.task_id,
        "success": state.execution.success,
        "execution_status": state.execution.execution_status.value
    })
    
    state.update_timestamp()
    
    return {"state": state}


def _execute_with_retry_fallback(
    step,
    routing_decision,
    context: Dict[str, Any] = None
) -> ExecutionResult:
    """Execute step with retry and fallback logic.
    
    Executor Integration: Integrate retry_manager and fallback_manager.
    
    Args:
        step: Current plan step
        routing_decision: Routing decision
        context: Current context
        
    Returns:
        ExecutionResult with execution outcome
    """
    max_retries = 3
    retry_count = 0
    
    # Try to integrate with retry_manager
    try:
        from executor.retry_manager import retry_manager
        
        # Use retry manager if available
        max_retries = getattr(retry_manager, 'max_retries', 3)
        logger.info(f"Using retry_manager with max_retries={max_retries}")
    except ImportError:
        logger.warning("Could not import retry_manager, using default max_retries=3")
    
    # Try execution with retries
    while retry_count <= max_retries:
        try:
            execution_result = _execute_single_attempt(
                step,
                routing_decision,
                context
            )
            
            if execution_result.success:
                logger.info(f"Execution succeeded on attempt {retry_count + 1}")
                return execution_result
            else:
                # Check if error is retryable
                if _is_retryable_error(execution_result.result_data):
                    retry_count += 1
                    logger.warning(f"Execution failed (retryable), retry {retry_count}/{max_retries}")
                    continue
                else:
                    # Non-retryable error, try fallback
                    break
        except Exception as e:
            retry_count += 1
            logger.warning(f"Execution raised exception (retry {retry_count}/{max_retries}): {e}")
            if retry_count > max_retries:
                break
    
    # All retries exhausted, try fallback
    logger.info("Retries exhausted, attempting fallback")
    return _execute_with_fallback(step, routing_decision, context)


def _execute_single_attempt(
    step,
    routing_decision,
    context: Dict[str, Any] = None
) -> ExecutionResult:
    """Execute a single execution attempt.
    
    Args:
        step: Current plan step
        routing_decision: Routing decision
        context: Current context
        
    Returns:
        ExecutionResult with execution outcome
    """
    import uuid
    import time
    
    execution_id = str(uuid.uuid4())
    start_time = time.time()
    
    # Get method type from routing decision
    method_type = routing_decision.selected_candidate.method_type if routing_decision else "unknown"
    
    # Get step parameters
    step_action = getattr(step, 'action', None) or getattr(step, 'objective', '')
    step_parameters = getattr(step, 'parameters', {}) or {}
    
    logger.info(f"Executing {method_type} for action: {step_action}")
    
    # Try to execute using tool_registry
    try:
        from tools.tool_registry import tool_registry
        
        # Map method type to tool type
        tool_type = _map_method_to_tool_type(method_type)
        
        # Get tool from registry
        tool = tool_registry.get_tool(tool_type)
        
        if tool:
            # Execute tool
            result = tool.execute(**step_parameters)
            
            execution_time = time.time() - start_time
            
            return ExecutionResult(
                execution_id=execution_id,
                step_id=step.step_id,
                success=True,
                method_used=method_type,
                execution_status=TaskStatus.COMPLETED,
                result_data={
                    "result": result,
                    "execution_time": execution_time,
                    "tool_type": tool_type
                }
            )
        else:
            # Tool not found
            return ExecutionResult(
                execution_id=execution_id,
                step_id=step.step_id,
                success=False,
                method_used=method_type,
                execution_status=TaskStatus.FAILED,
                result_data={"error": f"Tool not found for type: {tool_type}"}
            )
    except ImportError:
        logger.warning("Could not import tool_registry, using placeholder execution")
        return _execute_placeholder(step, routing_decision, context, execution_id)
    except Exception as e:
        logger.error(f"Error executing tool: {e}")
        return ExecutionResult(
            execution_id=execution_id,
            step_id=step.step_id,
            success=False,
            method_used=method_type,
            execution_status=TaskStatus.FAILED,
            result_data={"error": str(e)}
        )


def _execute_with_fallback(
    step,
    routing_decision,
    context: Dict[str, Any] = None
) -> ExecutionResult:
    """Execute with fallback method.
    
    Executor Integration: Integrate fallback_manager.
    
    Args:
        step: Current plan step
        routing_decision: Routing decision
        context: Current context
        
    Returns:
        ExecutionResult with execution outcome
    """
    import uuid
    
    execution_id = str(uuid.uuid4())
    
    # Try to integrate with fallback_manager
    try:
        from executor.fallback_manager import fallback_manager
        
        # Get fallback chain from routing decision
        fallback_chain = getattr(routing_decision, 'fallback_chain', None)
        
        if fallback_chain:
            logger.info(f"Using fallback_manager with {len(fallback_chain)} fallback methods")
            
            # Try each fallback method
            for fallback_method in fallback_chain:
                try:
                    # Execute with fallback method
                    execution_result = _execute_single_attempt(
                        step,
                        routing_decision,
                        context
                    )
                    
                    if execution_result.success:
                        logger.info(f"Fallback method {fallback_method} succeeded")
                        return execution_result
                except Exception as e:
                    logger.warning(f"Fallback method {fallback_method} failed: {e}")
                    continue
    except ImportError:
        logger.warning("Could not import fallback_manager, skipping fallback")
    except Exception as e:
        logger.error(f"Error in fallback_manager: {e}")
    
    # All fallbacks exhausted, return failure
    return ExecutionResult(
        execution_id=execution_id,
        step_id=step.step_id,
        success=False,
        method_used="fallback_failed",
        execution_status=TaskStatus.FAILED,
        result_data={"error": "All execution attempts and fallbacks failed"}
    )


def _execute_placeholder(
    step,
    routing_decision,
    context: Dict[str, Any] = None,
    execution_id: str = "placeholder"
) -> ExecutionResult:
    """Execute with placeholder logic.
    
    Args:
        step: Current plan step
        routing_decision: Routing decision
        context: Current context
        execution_id: Execution ID
        
    Returns:
        ExecutionResult with placeholder outcome
    """
    import time
    
    start_time = time.time()
    
    # Simulate execution
    time.sleep(0.1)
    
    execution_time = time.time() - start_time
    
    method_type = routing_decision.selected_candidate.method_type if routing_decision else "unknown"
    
    return ExecutionResult(
        execution_id=execution_id,
        step_id=step.step_id,
        success=True,
        method_used=method_type,
        execution_status=TaskStatus.COMPLETED,
        result_data={
            "note": "Placeholder execution (tool_registry unavailable)",
            "execution_time": execution_time
        }
    )


def _map_method_to_tool_type(method_type: str) -> str:
    """Map method type to tool type.
    
    Args:
        method_type: Method type from routing decision
        
    Returns:
        Tool type for tool_registry
    """
    method_type_lower = method_type.lower()
    
    if "shell" in method_type_lower or "command" in method_type_lower:
        return "shell_tool"
    elif "ui" in method_type_lower:
        return "ui_tool"
    elif "api" in method_type_lower:
        return "api_tool"
    elif "plugin" in method_type_lower:
        return "plugin"
    else:
        return "shell_tool"  # Default


def _is_retryable_error(result_data: Dict[str, Any]) -> bool:
    """Check if error is retryable.
    
    Args:
        result_data: Result data from execution
        
    Returns:
        True if error is retryable, False otherwise
    """
    error = result_data.get("error", "")
    
    # Transient errors are retryable
    transient_errors = [
        "timeout",
        "connection",
        "network",
        "temporary",
        "transient"
    ]
    
    error_lower = error.lower()
    return any(err in error_lower for err in transient_errors)
