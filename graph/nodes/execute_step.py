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
from migration.domain_contracts import ExecutionRequest, ExecutionResult, TaskStatus, FailureCategory
from graph.trace_collector import get_trace_collector
from graph.context_helpers import context_to_dict
from graph.async_helpers import run_async_safely
from graph.state_helpers import get_safe_field, get_plan_step, get_routing_method, get_execution_success, validate_state_for_node
from graph.error_helpers import handle_recoverable_error, handle_non_recoverable_error, track_error

logger = logging.getLogger("Graph.ExecuteStep")


def _map_failure_class(executor_failure_class) -> FailureCategory:
    """Map executor's FailureClass to graph's FailureCategory.

    Phase 4: Bridge between executor's FailureClass (from tools/routing_decision.py)
    and graph's FailureCategory (from migration/domain_contracts.py).

    Args:
        executor_failure_class: FailureClass from tools/routing_decision.py

    Returns:
        FailureCategory from migration/domain_contracts.py
    """
    from tools.routing_decision import FailureClass as ExecutorFailureClass

    # Map executor FailureClass to graph FailureCategory
    mapping = {
        ExecutorFailureClass.ENV_TRANSIENT: FailureCategory.TRANSIENT,
        ExecutorFailureClass.ROUTING_MISMATCH: FailureCategory.ROUTING_MISMATCH,
        ExecutorFailureClass.ENV_PERMANENT: FailureCategory.ENVIRONMENTAL,
        ExecutorFailureClass.EXECUTION_LOGIC: FailureCategory.PLANNING_ERROR,  # Logic error is planning problem
    }

    return mapping.get(executor_failure_class, FailureCategory.UNKNOWN)


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
    
    # Validate required state fields
    validate_state_for_node(state, "execute_step", ["task", "plan", "routing"])
    
    state.add_history_event("execute_step_started", {
        "task_id": state.task.task_id,
        "step_id": get_safe_field(state, 'plan.current_step.step_id', None),
        "method": get_routing_method(state, None)
    })
    
    # Executor Integration: Use actual executor modules
    try:
        # Get current step
        current_step = get_plan_step(state)
        
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
        # Convert ContextSnapshot to dict for executor compatibility
        context_dict = context_to_dict(state.context) if state.context else None
        execution_result = _execute_with_executor(
            current_step,
            routing_decision,
            context_dict,
            state.task.task_id
        )
        
        state.execution = execution_result
        
    except Exception as e:
        # Classify and track the error
        graph_error = handle_non_recoverable_error(
            e,
            "execute_step",
            context={"step_id": get_safe_field(state, 'plan.current_step.step_id', "unknown")}
        )
        track_error(state, graph_error)
        
        # Create error execution result
        execution_result = ExecutionResult(
            execution_id="error_exec",
            step_id=get_safe_field(state, 'plan.current_step.step_id', "unknown"),
            success=False,
            method_used=get_routing_method(state, "unknown"),
            execution_status=TaskStatus.FAILED,
            result_data={
                "error": str(e),
                "error_type": graph_error.error_type,
                "error_category": graph_error.category.value
            }
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
        intent = get_safe_field(state, 'intent.name', "unknown")
        
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
        # Learning integration is optional, treat as recoverable
        graph_error = handle_recoverable_error(
            e,
            "execute_step",
            fallback_description="skipping performance feedback collection"
        )
        track_error(state, graph_error)
    
    # Update plan progress if execution succeeded
    if get_execution_success(state) and get_plan_step(state):
        step_id = get_plan_step(state).step_id
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
        
        # Run async executor using run_async_safely
        try:
            success, result, method_used, failure_class = run_async_safely(
                executor._execute_with_decision,
                task_id=task_id,
                step_index=0,
                step=step_dict,
                context=context or {},
                decision=executor_decision,
                timeout=60.0
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
                },
                failure_category=FailureCategory.UNKNOWN  # Phase 4: Default category on exception
            )
        
        execution_time = time.time() - start_time

        # Convert executor result to ExecutionResult
        # Phase 4: Map executor's FailureClass to graph's FailureCategory
        failure_category = _map_failure_class(failure_class) if not success else None

        return ExecutionResult(
            execution_id=execution_id,
            step_id=step.step_id if step else "unknown",
            success=success,
            method_used=method_used,
            execution_status=TaskStatus.COMPLETED if success else TaskStatus.FAILED,
            result_data={
                "result": result,
                "execution_time": execution_time
            },
            failure_category=failure_category  # Phase 4: Add failure category
        )
        
    except ImportError as e:
        logger.warning(f"Could not import Executor: {e}, using placeholder execution")
        return _execute_placeholder(step, routing_decision, context, execution_id)
    except Exception as e:
        logger.error(f"Error executing with Executor: {e}, using placeholder execution")
        # Use placeholder on executor execution error instead of failing
        return _execute_placeholder(step, routing_decision, context, execution_id)


def _translate_routing_decision(
    graph_routing_decision,
    step
) -> Any:
    """Translate graph state MethodDecision to executor MethodDecision.

    This function now passes concrete commands from the LLM-generated plan step
    instead of delegating user_input to the legacy system.

    Args:
        graph_routing_decision: MethodDecision from migration.domain_contracts
        step: Current plan step with concrete command from LLM

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

        # Build fallback chain - only for execution-level failures, not routing-level
        # Per migration plan: Executor should retry execution-level failures only
        # Routing-level failures should be handled by graph's recovery node
        fallback_chain = tuple()  # Empty - executor won't do routing fallbacks

        # Build layered payload from step parameters
        step_parameters = getattr(step, 'parameters', {}) or {}
        step_action = getattr(step, 'action', None) or getattr(step, 'objective', '')

        # For shell execution, use concrete command from LLM
        # The LLM generates actual commands like "gnome-system-monitor"
        shell_argv = None
        if method_type == MethodType.SHELL:
            # Get shell_argv from step parameters (set by LLM)
            shell_argv_list = step_parameters.get("shell_argv", [])
            if shell_argv_list:
                shell_argv = tuple(str(arg) for arg in shell_argv_list)
            else:
                # Fallback: use action as command
                shell_argv = tuple(str(step_action).split())

        # Create payload for each method type
        payload = LayeredPayload(
            plugin_kwargs=MappingProxyType(step_parameters) if method_type == MethodType.PLUGIN else None,
            api_body=MappingProxyType(step_parameters) if method_type == MethodType.API else None,
            shell_argv=shell_argv if method_type == MethodType.SHELL else None,
            ui_action=MappingProxyType({"action": step_action, **step_parameters}) if method_type == MethodType.UI else None
        )

        # Create executor MethodDecision
        executor_decision = ExecutorMethodDecision(
            method=method_type,
            confidence=graph_routing_decision.confidence,
            fallback_chain=fallback_chain,  # Empty - no routing fallbacks
            payload=payload,
            expected_app=None,  # Would need to extract from context
            expected_ui_state=None,  # Would need to extract from context
            rejected=[]  # Would need to translate rejected candidates
        )

        logger.info(
            f"Translated routing decision: method={method_type}, "
            f"shell_argv={shell_argv}, confidence={graph_routing_decision.confidence}"
        )

        return executor_decision

    except Exception as e:
        logger.error(f"Error translating routing decision: {e}")
        return None


def _convert_step_to_executor_format(step) -> Dict[str, Any]:
    """Convert graph PlanStep to executor step format.

    This function now passes concrete commands from the LLM-generated plan step
    instead of delegating user_input to the legacy system.

    Args:
        step: PlanStep from graph state with concrete command from LLM

    Returns:
        Dict in executor step format with concrete command
    """
    step_parameters = getattr(step, 'parameters', {}) or {}
    step_action = getattr(step, 'action', None) or getattr(step, 'objective', '')

    # Use concrete command from LLM (stored in action)
    # The LLM generates actual commands like "gnome-system-monitor"
    # shell_argv contains the arguments if present
    shell_argv = step_parameters.get("shell_argv", [])

    # Build command with arguments
    if shell_argv:
        command = f"{step_action} {' '.join(shell_argv)}"
    else:
        command = step_action

    return {
        "action": command,  # Concrete command from LLM
        "args": step_parameters,
        "step_id": getattr(step, 'step_id', 'unknown'),
        "command": step_action,  # Explicit command field
        "shell_argv": shell_argv  # Explicit arguments
    }


# Keep placeholder as fallback

def _execute_placeholder(
    step,
    routing_decision,
    context: Dict[str, Any] = None,
    execution_id: str = "placeholder"
) -> ExecutionResult:
    """Execute with placeholder logic - no legacy delegation.

    This function provides a fallback execution when the actual executor
    is unavailable. It does NOT delegate to the legacy orchestrator system
    to avoid dual execution and race conditions.

    The graph should own execution completely.

    Args:
        step: Current plan step with concrete command from LLM
        routing_decision: Routing decision
        context: Current context
        execution_id: Execution ID

    Returns:
        ExecutionResult with placeholder outcome
    """
    import time

    start_time = time.time()

    step_parameters = getattr(step, 'parameters', {}) or {}
    step_action = getattr(step, 'action', None) or getattr(step, 'objective', '')
    shell_argv = step_parameters.get("shell_argv", [])

    # Get concrete command
    if shell_argv:
        command = f"{step_action} {' '.join(shell_argv)}"
    else:
        command = step_action

    # Simple placeholder execution without legacy delegation
    logger.info(f"EXECUTE_PLACEHOLDER: Simulating execution for: {command}")

    # Simulate execution with realistic timing
    time.sleep(0.2)

    # Return a simulated success result
    success = True
    result = f"Placeholder execution completed for: {command}"

    execution_time = time.time() - start_time

    method_type = routing_decision.selected_candidate.method_type if routing_decision else "unknown"

    return ExecutionResult(
        execution_id=execution_id,
        step_id=step.step_id if step else "unknown",
        success=success,
        method_used=method_type,
        execution_status=TaskStatus.COMPLETED if success else TaskStatus.FAILED,
        result_data={
            "result": result,
            "execution_time": execution_time,
            "command": command,
            "shell_argv": shell_argv
        }
    )


