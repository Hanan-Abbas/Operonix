"""
Reflection Node — Operonix Graph
────────────────────────────────

Reflection node: Analyzes execution results and generates learning feedback.
Per migration plan Phase 9: Observability & Execution Trace
"""
from __future__ import annotations

import logging
from typing import Dict, Any
from datetime import timezone, datetime

from migration.graph_state import OperonixState
from graph.trace_collector import get_trace_collector
from graph.learning_integration import get_learning_integration

logger = logging.getLogger("Graph.Reflect")


def reflect_node(state: OperonixState) -> Dict[str, Any]:
    """Reflection node: Analyze execution results and generate learning feedback.
    
    This node:
    - Analyzes execution results (success/failure, errors, performance)
    - Generates learning feedback for routing decisions
    - Updates learning system with performance data
    - Collects reflection trace event
    - May trigger replanning if significant issues detected
    
    Per migration plan Phase 9:
    - Reflection analyzes what happened and why
    - Generates feedback for learning system
    - Updates routing preferences based on success/failure
    
    Args:
        state: Current OperonixState (should have execution results)
        
    Returns:
        Dict with updated state including reflection data
    """
    logger.info(f"REFLECT: Analyzing execution results for task {state.task.task_id}")
    
    state.add_history_event("reflection_started", {
        "task_id": state.task.task_id
    })
    
    # Initialize reflection data
    reflection_data = {
        "task_id": state.task.task_id,
        "success": None,
        "execution_time": None,
        "errors": [],
        "learning_feedback": {},
        "recommendations": []
    }
    
    try:
        # Analyze execution results
        if state.execution:
            execution_success = state.execution.execution_status == "completed"
            reflection_data["success"] = execution_success
            
            if state.execution.error:
                reflection_data["errors"].append(state.execution.error)
            
            logger.info(f"REFLECT: Execution success: {execution_success}")
        
        # Analyze routing decision for learning feedback
        if state.routing and state.execution:
            learning_integration = get_learning_integration()
            
            # Generate performance feedback
            method_type = state.routing.selected_candidate.method_type if state.routing.selected_candidate else "unknown"
            success = state.execution.execution_status == "completed"
            
            learning_feedback = learning_integration.collect_performance_feedback(
                task_id=state.task.task_id,
                method_type=method_type,
                success=success,
                confidence=state.routing.selected_candidate.confidence if state.routing.selected_candidate else 0.0
            )
            
            reflection_data["learning_feedback"] = learning_feedback
            logger.info(f"REFLECT: Learning feedback collected for method: {method_type}")
        
        # Analyze verification results
        if state.verification:
            if state.verification.status == "FAILED":
                reflection_data["errors"].append(f"Verification failed: {state.verification.failure_reason if state.verification.failure_reason else 'unknown'}")
                reflection_data["recommendations"].append("Consider alternative execution method")
            
            if state.verification.status == "UNCERTAIN_OUTCOME":
                reflection_data["recommendations"].append("Add additional verification steps")
        
        # Analyze recovery attempts
        if state.recovery and state.recovery.recovery_attempted:
            reflection_data["recommendations"].append(f"Recovery attempted: {state.recovery.recovery_strategy}")
            if state.recovery.recovery_successful:
                reflection_data["recommendations"].append("Recovery was successful")
            else:
                reflection_data["errors"].append("Recovery failed")
                reflection_data["recommendations"].append("Consider different recovery strategy")
        
        logger.info(f"REFLECT: Reflection completed for task {state.task.task_id}")
        
    except Exception as e:
        logger.error(f"REFLECT: Error during reflection: {e}", exc_info=True)
        reflection_data["errors"].append(f"Reflection error: {str(e)}")
    
    # Store reflection data in state (outside try/except to ensure it's always set)
    from migration.domain_contracts import ReflectionResult, OutcomeGrade
    
    # Determine outcome grade
    if reflection_data["success"]:
        outcome_grade = OutcomeGrade.EXCELLENT
    elif reflection_data["errors"]:
        outcome_grade = OutcomeGrade.FAILED
    else:
        outcome_grade = OutcomeGrade.ACCEPTABLE
    
    state.reflection = ReflectionResult(
        outcome=outcome_grade,
        failure_category=None  # TODO: Map errors to FailureCategory if needed
    )
    
    # Collect trace event for reflection
    trace_collector = get_trace_collector()
    trace_collector.collect_reflection(
        task_id=state.task.task_id,
        reflection_data=reflection_data
    )
    
    state.add_history_event("reflection_completed", {
        "task_id": state.task.task_id,
        "success": reflection_data.get("success"),
        "error_count": len(reflection_data.get("errors", [])),
        "recommendation_count": len(reflection_data.get("recommendations", []))
    })
    
    state.update_timestamp()
    
    return {"reflection": state.reflection}
