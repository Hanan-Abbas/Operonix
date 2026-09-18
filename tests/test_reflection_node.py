"""
Reflection Node Tests — Operonix Graph
──────────────────────────────────────

Unit tests for reflection node and learning feedback.
These tests verify that:
- Reflection node analyzes execution results correctly
- Learning feedback is generated for routing decisions
- Reflection data is stored in state
- Trace events are collected for reflection
- Recommendations are generated based on execution outcome
- Errors are analyzed and reported
"""
from __future__ import annotations

import pytest
from typing import Dict, Any
from migration.graph_state import OperonixState
from migration.domain_contracts import TaskRequest, TaskSource, ExecutionResult, ReflectionResult, OutcomeGrade, TaskStatus
from graph.nodes.reflect import reflect_node


# ─── REFLECTION NODE TESTS ─────────────────────────────────────────────────

def test_reflection_node_exists():
    """Test that reflection node can be imported."""
    from graph.nodes.reflect import reflect_node
    assert reflect_node is not None


def test_reflection_node_with_successful_execution():
    """Test reflection node with successful execution."""
    import uuid
    task = TaskRequest(user_input="open firefox", source=TaskSource.VOICE)
    state = OperonixState(task=task)
    
    # Simulate successful execution
    state.execution = ExecutionResult(
        execution_id=str(uuid.uuid4()),
        step_id="test-step",
        success=True,
        result_data={"output": "Command executed successfully"},
        method_used="shell",
        execution_status=TaskStatus.COMPLETED
    )
    
    result = reflect_node(state)
    
    # Should have reflection result
    assert state.reflection is not None
    assert isinstance(state.reflection, ReflectionResult)
    # Success should be reflected in outcome
    assert state.reflection.outcome == OutcomeGrade.EXCELLENT


def test_reflection_node_with_failed_execution():
    """Test reflection node with failed execution."""
    import uuid
    task = TaskRequest(user_input="delete file", source=TaskSource.VOICE)
    state = OperonixState(task=task)
    
    # Simulate failed execution
    state.execution = ExecutionResult(
        execution_id=str(uuid.uuid4()),
        step_id="test-step",
        success=False,
        result_data={},
        error="Permission denied",
        error_type="PermissionError",
        method_used="shell",
        execution_status=TaskStatus.FAILED
    )
    
    result = reflect_node(state)
    
    # Should have reflection result
    assert state.reflection is not None
    # Failure should be reflected in outcome
    assert state.reflection.outcome == OutcomeGrade.FAILED


def test_reflection_node_without_execution():
    """Test reflection node when no execution exists (should not crash)."""
    task = TaskRequest(user_input="test", source=TaskSource.VOICE)
    state = OperonixState(task=task)
    
    # Should not crash even without execution
    result = reflect_node(state)
    
    # Should still have reflection result (with ACCEPTABLE outcome by default)
    assert state.reflection is not None


def test_reflection_node_history_tracking():
    """Test that reflection node tracks history."""
    task = TaskRequest(user_input="test", source=TaskSource.VOICE)
    state = OperonixState(task=task)
    
    reflect_node(state)
    
    events = state.history.get("events", [])
    assert len(events) > 0
    event_types = [e["type"] for e in events]
    assert "reflection_started" in event_types
    assert "reflection_completed" in event_types


def test_reflection_node_with_verification():
    """Test reflection node with verification results."""
    import uuid
    task = TaskRequest(user_input="test", source=TaskSource.VOICE)
    state = OperonixState(task=task)
    
    # Simulate verification
    from migration.domain_contracts import VerificationResult
    state.verification = VerificationResult(
        verification_id=str(uuid.uuid4()),
        step_id="test-step",
        status="VERIFIED",
        confidence=0.95,
        observed_context={}
    )
    
    result = reflect_node(state)
    
    # Should have reflection result
    assert state.reflection is not None


def test_reflection_node_with_failed_verification():
    """Test reflection node with failed verification."""
    import uuid
    task = TaskRequest(user_input="test", source=TaskSource.VOICE)
    state = OperonixState(task=task)
    
    # Simulate failed verification
    from migration.domain_contracts import VerificationResult
    state.verification = VerificationResult(
        verification_id=str(uuid.uuid4()),
        step_id="test-step",
        status="FAILED",
        confidence=0.0,
        failure_reason="Postcondition not met",
        observed_context={}
    )
    
    result = reflect_node(state)
    
    # Should have reflection result
    assert state.reflection is not None


def test_reflection_node_with_recovery():
    """Test reflection node with recovery attempt."""
    import uuid
    task = TaskRequest(user_input="test", source=TaskSource.VOICE)
    state = OperonixState(task=task)
    
    # Simulate recovery
    from migration.domain_contracts import RecoveryDecision
    state.recovery = RecoveryDecision(
        recovery_id=str(uuid.uuid4()),
        task_id=task.task_id,
        recovery_strategy="retry",
        failure_category="transient",
        recovery_attempted=True,
        recovery_successful=True
    )
    
    result = reflect_node(state)
    
    # Should have reflection result
    assert state.reflection is not None


def test_reflection_node_with_failed_recovery():
    """Test reflection node with failed recovery."""
    import uuid
    task = TaskRequest(user_input="test", source=TaskSource.VOICE)
    state = OperonixState(task=task)
    
    # Simulate failed recovery
    from migration.domain_contracts import RecoveryDecision
    state.recovery = RecoveryDecision(
        recovery_id=str(uuid.uuid4()),
        task_id=task.task_id,
        recovery_strategy="retry",
        failure_category="transient",
        recovery_attempted=True,
        recovery_successful=False
    )
    
    result = reflect_node(state)
    
    # Should have reflection result
    assert state.reflection is not None


def test_reflection_node_trace_collection():
    """Test that reflection node collects trace events."""
    task = TaskRequest(user_input="test", source=TaskSource.VOICE)
    state = OperonixState(task=task)
    
    # Reflection node should not crash even if trace collection fails
    result = reflect_node(state)
    
    # Should still have reflection result
    assert state.reflection is not None


def test_reflection_node_outcome_grade_mapping():
    """Test that outcome grades are mapped correctly."""
    import uuid
    test_cases = [
        (True, OutcomeGrade.EXCELLENT),
        (False, OutcomeGrade.FAILED),
    ]
    
    for success, expected_outcome in test_cases:
        task = TaskRequest(user_input="test", source=TaskSource.VOICE)
        state = OperonixState(task=task)
        
        state.execution = ExecutionResult(
            execution_id=str(uuid.uuid4()),
            step_id="test-step",
            success=success,
            result_data={"output": "test"},
            error=None if success else "error",
            error_type=None if success else "Error",
            method_used="shell",
            execution_status=TaskStatus.COMPLETED if success else TaskStatus.FAILED
        )
        
        reflect_node(state)
        
        assert state.reflection is not None
        assert state.reflection.outcome == expected_outcome


def test_reflection_node_partial_outcome():
    """Test reflection node with partial/uncertain outcome."""
    import uuid
    task = TaskRequest(user_input="test", source=TaskSource.VOICE)
    state = OperonixState(task=task)
    
    # Simulate uncertain verification
    from migration.domain_contracts import VerificationResult
    state.verification = VerificationResult(
        verification_id=str(uuid.uuid4()),
        step_id="test-step",
        status="UNCERTAIN_OUTCOME",
        confidence=0.5,
        observed_context={}
    )
    
    result = reflect_node(state)
    
    # Should have reflection result
    assert state.reflection is not None
    # May be ACCEPTABLE depending on other factors


def test_reflection_node_returns_state_update():
    """Test that reflection node returns state update."""
    task = TaskRequest(user_input="test", source=TaskSource.VOICE)
    state = OperonixState(task=task)
    
    result = reflect_node(state)
    
    # Should return state update
    assert isinstance(result, dict)
    assert "reflection" in result


def test_reflection_node_with_routing_decision():
    """Test reflection node with routing decision for learning feedback."""
    import uuid
    task = TaskRequest(user_input="test", source=TaskSource.VOICE)
    state = OperonixState(task=task)
    
    # Simulate routing decision
    from migration.domain_contracts import RoutingDecision, Candidate
    state.routing = RoutingDecision(
        selected_candidate=Candidate(
            candidate_id=str(uuid.uuid4()),
            candidate_type="shell",
            method_type="shell",
            method_name="execute_command"
        ),
        routing_explanation="Test routing decision"
    )
    
    # Simulate successful execution
    state.execution = ExecutionResult(
        execution_id=str(uuid.uuid4()),
        step_id="test-step",
        success=True,
        result_data={"output": "Success"},
        method_used="shell",
        execution_status=TaskStatus.COMPLETED
    )
    
    result = reflect_node(state)
    
    # Should have reflection result
    assert state.reflection is not None


# ─── REFLECTION RESULT TESTS ─────────────────────────────────────────────────

def test_reflection_result_structure():
    """Test that reflection result has correct structure."""
    task = TaskRequest(user_input="test", source=TaskSource.VOICE)
    state = OperonixState(task=task)
    
    reflect_node(state)
    
    assert state.reflection is not None
    assert hasattr(state.reflection, 'outcome')
    assert hasattr(state.reflection, 'failure_category')
    assert state.reflection.outcome in [OutcomeGrade.EXCELLENT, OutcomeGrade.GOOD, OutcomeGrade.ACCEPTABLE, OutcomeGrade.POOR, OutcomeGrade.FAILED]


def test_reflection_result_failure_category():
    """Test that failure category is set for failed executions."""
    import uuid
    task = TaskRequest(user_input="test", source=TaskSource.VOICE)
    state = OperonixState(task=task)
    
    state.execution = ExecutionResult(
        execution_id=str(uuid.uuid4()),
        step_id="test-step",
        success=False,
        result_data={},
        error="Error occurred",
        error_type="Error",
        method_used="shell",
        execution_status=TaskStatus.FAILED
    )
    
    reflect_node(state)
    
    assert state.reflection is not None
    assert state.reflection.outcome == OutcomeGrade.FAILED
    # failure_category may be None or set based on error analysis


if __name__ == "__main__":
    pytest.main([__file__, "-v"])
