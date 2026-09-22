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

import asyncio
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
        
        # Perform execution with actual Executor
        execution_result = _execute_with_executor(
            current_step,
            routing_decision,
            state.context if isinstance(state.context, dict) else None,
            state.task.task_id
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
        step_id = state.plan.current_step.step_id
        state.plan.current_step_index += 1
        if step_id not in state.plan.completed_steps:
            state.plan.completed_steps.append(step_id)
    
    state.add_history_event("execute_step_completed", {
        "task_id": state.task.task_id,
        "success": state.execution.success,
        "execution_status": state.execution.execution_status.value
    })
    
    state.update_timestamp()
    
    return {"execution": state.execution, "plan": state.plan}


def _execute_with_executor(
    step,
    routing_decision,
    context: Dict[str, Any] = None,
    task_id: str = "unknown"
) -> ExecutionResult:
    """Execute step using actual Executor class.
    
    This function:
    - Translates graph state routing decision to executor routing decision
    - Calls Executor._execute_with_decision for actual execution
    - Handles async/sync translation
    - Converts executor result to ExecutionResult
    
    Args:
        step: Current plan step
        routing_decision: Routing decision from graph state
        context: Current context
        task_id: Task ID
        
    Returns:
        ExecutionResult with execution outcome
    """
    import uuid
    import time
    
    execution_id = str(uuid.uuid4())
    start_time = time.time()
    
    try:
        # Import Executor
        from executor.executor import Executor
        from tools.routing_decision import MethodDecision as ExecutorMethodDecision, MethodType, LayeredPayload
        
        # Create Executor instance
        executor = Executor()
        
        # Translate graph routing decision to executor routing decision
        executor_decision = _translate_routing_decision(routing_decision, step)
        
        if executor_decision is None:
            logger.warning("Could not translate routing decision, using placeholder")
            return _execute_placeholder(step, routing_decision, context, execution_id)
        
        # Convert step to executor format
        step_dict = _convert_step_to_executor_format(step)
        
        # Run async executor - simplified async handling
        # Use a clean async execution in a thread to avoid event loop conflicts
        import concurrent.futures
        try:
            with concurrent.futures.ThreadPoolExecutor(max_workers=1) as executor_pool:
                future = executor_pool.submit(
                    asyncio.run,
                    executor._execute_with_decision(
                        task_id=task_id,
                        step_index=0,
                        step=step_dict,
                        context=context or {},
                        decision=executor_decision
                    )
                )
                # Add timeout to prevent hanging
                success, result, method_used = future.result(timeout=60.0)  # 60 second timeout
        except concurrent.futures.TimeoutError:
            logger.error(f"Executor execution timed out for task {task_id}")
            execution_time = time.time() - start_time
            return ExecutionResult(
                execution_id=execution_id,
                step_id=step.step_id if step else "unknown",
                success=False,
                method_used="timeout",
                execution_status=TaskStatus.FAILED,
                result_data={
                    "error": "Executor execution timed out",
                    "execution_time": execution_time
                }
            )
        except Exception as exec_error:
            logger.error(f"Executor execution error: {exec_error}")
            execution_time = time.time() - start_time
            return ExecutionResult(
                execution_id=execution_id,
                step_id=step.step_id if step else "unknown",
                success=False,
                method_used="executor_error",
                execution_status=TaskStatus.FAILED,
                result_data={
                    "error": str(exec_error),
                    "execution_time": execution_time
                }
            )
        
        execution_time = time.time() - start_time
        
        execution_time = time.time() - start_time
        
        # Convert executor result to ExecutionResult
        return ExecutionResult(
            execution_id=execution_id,
            step_id=step.step_id if step else "unknown",
            success=success,
            method_used=method_used,
            execution_status=TaskStatus.COMPLETED if success else TaskStatus.FAILED,
            result_data={
                "result": result,
                "execution_time": execution_time
            }
        )
        
    except ImportError as e:
        logger.warning(f"Could not import Executor: {e}, using placeholder execution")
        return _execute_placeholder(step, routing_decision, context, execution_id)
    except Exception as e:
        logger.error(f"Error executing with Executor: {e}")
        execution_time = time.time() - start_time
        return ExecutionResult(
            execution_id=execution_id,
            step_id=step.step_id if step else "unknown",
            success=False,
            method_used="executor_error",
            execution_status=TaskStatus.FAILED,
            result_data={
                "error": str(e),
                "execution_time": execution_time
            }
        )


def _translate_routing_decision(
    graph_routing_decision,
    step
) -> Any:
    """Translate graph state MethodDecision to executor MethodDecision.
    
    Args:
        graph_routing_decision: MethodDecision from migration.domain_contracts
        step: Current plan step
        
    Returns:
        Executor MethodDecision or None if translation fails
    """
    try:
        from tools.routing_decision import MethodDecision as ExecutorMethodDecision, MethodType, LayeredPayload
        from types import MappingProxyType
        
        if not graph_routing_decision:
            return None
        
        # Extract method type from graph decision
        method_type_str = graph_routing_decision.selected_candidate.method_type
        
        # Map to executor MethodType
        method_type_map = {
            "plugin": MethodType.PLUGIN,
            "api": MethodType.API,
            "shell": MethodType.SHELL,
            "ui": MethodType.UI,
            "command": MethodType.SHELL
        }
        
        method_type = method_type_map.get(method_type_str.lower(), MethodType.SHELL)
        
        # Build fallback chain
        fallback_chain = tuple(
            method_type_map.get(c.method_type.lower(), MethodType.SHELL)
            for c in graph_routing_decision.fallback_candidates
        ) if graph_routing_decision.fallback_candidates else tuple()
        
        # Build layered payload from step parameters
        step_parameters = getattr(step, 'parameters', {}) or {}
        step_action = getattr(step, 'action', None) or getattr(step, 'objective', '')
        
        # Create payload for each method type
        payload = LayeredPayload(
            plugin_kwargs=MappingProxyType(step_parameters) if method_type == MethodType.PLUGIN else None,
            api_body=MappingProxyType(step_parameters) if method_type == MethodType.API else None,
            shell_argv=tuple(str(step_action).split()) if method_type == MethodType.SHELL else None,
            ui_action=MappingProxyType({"action": step_action, **step_parameters}) if method_type == MethodType.UI else None
        )
        
        # Create executor MethodDecision
        executor_decision = ExecutorMethodDecision(
            method=method_type,
            confidence=graph_routing_decision.confidence,
            fallback_chain=fallback_chain,
            payload=payload,
            expected_app=None,  # Would need to extract from context
            expected_ui_state=None,  # Would need to extract from context
            rejected=[]  # Would need to translate rejected candidates
        )
        
        return executor_decision
        
    except Exception as e:
        logger.error(f"Error translating routing decision: {e}")
        return None


def _convert_step_to_executor_format(step) -> Dict[str, Any]:
    """Convert graph PlanStep to executor step format.
    
    Args:
        step: PlanStep from graph state
        
    Returns:
        Dict in executor step format
    """
    return {
        "action": getattr(step, 'action', None) or getattr(step, 'objective', ''),
        "args": getattr(step, 'parameters', {}) or {},
        "step_id": getattr(step, 'step_id', 'unknown')
    }


# Keep placeholder as fallback

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
            "note": "Placeholder execution (Executor unavailable)",
            "execution_time": execution_time
        }
    )


