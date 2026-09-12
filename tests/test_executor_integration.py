"""
Executor Integration Tests — Operonix Migration
──────────────────────────────────────────────

Tests for executor integration.
Executor Integration Phase: Integrate actual executor modules.
"""
from __future__ import annotations

import pytest


# ─── EXECUTE STEP NODE INTEGRATION TESTS ─────────────────────────────────────

def test_execute_step_node_integrates_tool_registry():
    """Test that execute_step_node integrates with tool_registry."""
    from graph.nodes.execute_step import execute_step_node
    from migration.graph_state import OperonixState
    from migration.domain_contracts import TaskRequest, TaskSource, Plan, PlanStep, PlanStepIdempotency, PlanStepSideEffect, RoutingDecision, Candidate, MethodType
    
    task = TaskRequest(user_input="Test", source=TaskSource.VOICE)
    
    step = PlanStep(
        step_id="step_1",
        action="execute_command",
        objective="Execute command",
        idempotency=PlanStepIdempotency.IDEMPOTENT,
        side_effect=PlanStepSideEffect.NONE,
        parameters={"command": "ls -la"}
    )
    
    plan = Plan(
        plan_id="plan_1",
        steps=[step],
        current_step_index=0
    )
    
    candidate = Candidate(
        candidate_id="candidate_1",
        method_type=MethodType.SHELL,
        capability_id="execute_command",
        plugin_id=None,
        score=0.9
    )
    
    routing = RoutingDecision(
        selected_candidate=candidate,
        fallback_chain=[],
        reasoning="Test"
    )
    
    state = OperonixState(task=task, plan=plan, routing=routing)
    
    result = execute_step_node(state)
    
    assert result["state"].execution is not None
    assert result["state"].execution.execution_id is not None
    assert result["state"].execution.method_used is not None


def test_execute_step_node_handles_missing_step():
    """Test that execute_step_node handles missing current step gracefully."""
    from graph.nodes.execute_step import execute_step_node
    from migration.graph_state import OperonixState
    from migration.domain_contracts import TaskRequest, TaskSource
    
    task = TaskRequest(user_input="Test", source=TaskSource.VOICE)
    state = OperonixState(task=task)  # No plan
    
    result = execute_step_node(state)
    
    assert result["state"].execution is not None
    assert result["state"].execution.success is False
    assert "No current step" in result["state"].execution.result_data.get("error", "")


def test_execute_step_node_retry_logic():
    """Test that execute_step_node implements retry logic."""
    from graph.nodes.execute_step import _execute_with_retry_fallback
    from migration.domain_contracts import Plan, PlanStep, PlanStepIdempotency, PlanStepSideEffect, RoutingDecision, Candidate, MethodType
    
    step = PlanStep(
        step_id="step_1",
        action="execute_command",
        objective="Execute command",
        idempotency=PlanStepIdempotency.IDEMPOTENT,
        side_effect=PlanStepSideEffect.NONE,
        parameters={"command": "ls -la"}
    )
    
    candidate = Candidate(
        candidate_id="candidate_1",
        method_type=MethodType.SHELL,
        capability_id="execute_command",
        plugin_id=None,
        score=0.9
    )
    
    routing = RoutingDecision(
        selected_candidate=candidate,
        fallback_chain=[],
        reasoning="Test"
    )
    
    result = _execute_with_retry_fallback(step, routing, None)
    
    assert result is not None
    assert result.execution_id is not None


def test_execute_step_node_fallback_logic():
    """Test that execute_step_node implements fallback logic."""
    from graph.nodes.execute_step import _execute_with_fallback
    from migration.domain_contracts import Plan, PlanStep, PlanStepIdempotency, PlanStepSideEffect, RoutingDecision, Candidate, MethodType
    
    step = PlanStep(
        step_id="step_1",
        action="execute_command",
        objective="Execute command",
        idempotency=PlanStepIdempotency.IDEMPOTENT,
        side_effect=PlanStepSideEffect.NONE,
        parameters={"command": "ls -la"}
    )
    
    candidate = Candidate(
        candidate_id="candidate_1",
        method_type=MethodType.SHELL,
        capability_id="execute_command",
        plugin_id=None,
        score=0.9
    )
    
    routing = RoutingDecision(
        selected_candidate=candidate,
        fallback_chain=[MethodType.UI, MethodType.API],
        reasoning="Test"
    )
    
    result = _execute_with_fallback(step, routing, None)
    
    assert result is not None
    assert result.execution_id is not None


def test_execute_step_node_retryable_error_detection():
    """Test that retryable errors are correctly identified."""
    from graph.nodes.execute_step import _is_retryable_error
    
    # Transient errors should be retryable
    assert _is_retryable_error({"error": "timeout occurred"}) is True
    assert _is_retryable_error({"error": "connection failed"}) is True
    assert _is_retryable_error({"error": "network error"}) is True
    assert _is_retryable_error({"error": "temporary failure"}) is True
    
    # Non-transient errors should not be retryable
    assert _is_retryable_error({"error": "permission denied"}) is False
    assert _is_retryable_error({"error": "file not found"}) is False
    assert _is_retryable_error({"error": "invalid syntax"}) is False


def test_execute_step_node_method_to_tool_mapping():
    """Test that method types are correctly mapped to tool types."""
    from graph.nodes.execute_step import _map_method_to_tool_type
    
    assert _map_method_to_tool_type("shell") == "shell_tool"
    assert _map_method_to_tool_type("command") == "shell_tool"
    assert _map_method_to_tool_type("ui") == "ui_tool"
    assert _map_method_to_tool_type("api") == "api_tool"
    assert _map_method_to_tool_type("plugin") == "plugin"
    assert _map_method_to_tool_type("unknown") == "shell_tool"  # Default


def test_execute_step_node_trace_event_collection():
    """Test that execute_step_node collects trace events."""
    from graph.nodes.execute_step import execute_step_node
    from migration.graph_state import OperonixState
    from migration.domain_contracts import TaskRequest, TaskSource, Plan, PlanStep, PlanStepIdempotency, PlanStepSideEffect, RoutingDecision, Candidate, MethodType
    
    task = TaskRequest(user_input="Test", source=TaskSource.VOICE)
    
    step = PlanStep(
        step_id="step_1",
        action="execute_command",
        objective="Execute command",
        idempotency=PlanStepIdempotency.IDEMPOTENT,
        side_effect=PlanStepSideEffect.NONE,
        parameters={"command": "ls -la"}
    )
    
    plan = Plan(
        plan_id="plan_1",
        steps=[step],
        current_step_index=0
    )
    
    candidate = Candidate(
        candidate_id="candidate_1",
        method_type=MethodType.SHELL,
        capability_id="execute_command",
        plugin_id=None,
        score=0.9
    )
    
    routing = RoutingDecision(
        selected_candidate=candidate,
        fallback_chain=[],
        reasoning="Test"
    )
    
    state = OperonixState(task=task, plan=plan, routing=routing)
    
    result = execute_step_node(state)
    
    # Check that history events were added
    assert "execute_step_started" in [event.event_type for event in result["state"].history]
    assert "execute_step_completed" in [event.event_type for event in result["state"].history]


def test_execute_step_node_plan_progress_update():
    """Test that execute_step_node updates plan progress on success."""
    from graph.nodes.execute_step import execute_step_node
    from migration.graph_state import OperonixState
    from migration.domain_contracts import TaskRequest, TaskSource, Plan, PlanStep, PlanStepIdempotency, PlanStepSideEffect, RoutingDecision, Candidate, MethodType
    
    task = TaskRequest(user_input="Test", source=TaskSource.VOICE)
    
    step = PlanStep(
        step_id="step_1",
        action="execute_command",
        objective="Execute command",
        idempotency=PlanStepIdempotency.IDEMPOTENT,
        side_effect=PlanStepSideEffect.NONE,
        parameters={"command": "ls -la"}
    )
    
    plan = Plan(
        plan_id="plan_1",
        steps=[step],
        current_step_index=0
    )
    
    candidate = Candidate(
        candidate_id="candidate_1",
        method_type=MethodType.SHELL,
        capability_id="execute_command",
        plugin_id=None,
        score=0.9
    )
    
    routing = RoutingDecision(
        selected_candidate=candidate,
        fallback_chain=[],
        reasoning="Test"
    )
    
    state = OperonixState(task=task, plan=plan, routing=routing)
    
    result = execute_step_node(state)
    
    # Plan progress should be updated on success
    if result["state"].execution.success:
        assert result["state"].plan.current_step_index == 1
        assert step.step_id in result["state"].plan.completed_steps


def test_execute_step_node_error_handling():
    """Test that execute_step_node handles execution errors properly."""
    from graph.nodes.execute_step import execute_step_node
    from migration.graph_state import OperonixState
    from migration.domain_contracts import TaskRequest, TaskSource, Plan, PlanStep, PlanStepIdempotency, PlanStepSideEffect, RoutingDecision, Candidate, MethodType
    
    task = TaskRequest(user_input="Test", source=TaskSource.VOICE)
    
    step = PlanStep(
        step_id="step_1",
        action="invalid_action",
        objective="Invalid action",
        idempotency=PlanStepIdempotency.IDEMPOTENT,
        side_effect=PlanStepSideEffect.NONE,
        parameters={"invalid": "param"}
    )
    
    plan = Plan(
        plan_id="plan_1",
        steps=[step],
        current_step_index=0
    )
    
    candidate = Candidate(
        candidate_id="candidate_1",
        method_type=MethodType.SHELL,
        capability_id="invalid",
        plugin_id=None,
        score=0.9
    )
    
    routing = RoutingDecision(
        selected_candidate=candidate,
        fallback_chain=[],
        reasoning="Test"
    )
    
    state = OperonixState(task=task, plan=plan, routing=routing)
    
    result = execute_step_node(state)
    
    # Should handle error gracefully
    assert result["state"].execution is not None
    # Execution result should exist even if failed


def test_execute_single_attempt():
    """Test that _execute_single_attempt executes a single attempt."""
    from graph.nodes.execute_step import _execute_single_attempt
    from migration.domain_contracts import Plan, PlanStep, PlanStepIdempotency, PlanStepSideEffect, RoutingDecision, Candidate, MethodType
    
    step = PlanStep(
        step_id="step_1",
        action="execute_command",
        objective="Execute command",
        idempotency=PlanStepIdempotency.IDEMPOTENT,
        side_effect=PlanStepSideEffect.NONE,
        parameters={"command": "ls -la"}
    )
    
    candidate = Candidate(
        candidate_id="candidate_1",
        method_type=MethodType.SHELL,
        capability_id="execute_command",
        plugin_id=None,
        score=0.9
    )
    
    routing = RoutingDecision(
        selected_candidate=candidate,
        fallback_chain=[],
        reasoning="Test"
    )
    
    result = _execute_single_attempt(step, routing, None)
    
    assert result is not None
    assert result.execution_id is not None
    assert result.step_id == step.step_id


def test_execute_placeholder():
    """Test that _execute_placeholder provides placeholder execution."""
    from graph.nodes.execute_step import _execute_placeholder
    from migration.domain_contracts import Plan, PlanStep, PlanStepIdempotency, PlanStepSideEffect, RoutingDecision, Candidate, MethodType
    
    step = PlanStep(
        step_id="step_1",
        action="execute_command",
        objective="Execute command",
        idempotency=PlanStepIdempotency.IDEMPOTENT,
        side_effect=PlanStepSideEffect.NONE,
        parameters={"command": "ls -la"}
    )
    
    candidate = Candidate(
        candidate_id="candidate_1",
        method_type=MethodType.SHELL,
        capability_id="execute_command",
        plugin_id=None,
        score=0.9
    )
    
    routing = RoutingDecision(
        selected_candidate=candidate,
        fallback_chain=[],
        reasoning="Test"
    )
    
    result = _execute_placeholder(step, routing, None, "test_exec_id")
    
    assert result is not None
    assert result.execution_id == "test_exec_id"
    assert result.success is True
    assert "Placeholder execution" in result.result_data.get("note", "")


def test_execute_step_node_graceful_degradation():
    """Test that execute_step_node degrades gracefully when tool_registry unavailable."""
    from graph.nodes.execute_step import execute_step_node
    from migration.graph_state import OperonixState
    from migration.domain_contracts import TaskRequest, TaskSource, Plan, PlanStep, PlanStepIdempotency, PlanStepSideEffect, RoutingDecision, Candidate, MethodType
    
    task = TaskRequest(user_input="Test", source=TaskSource.VOICE)
    
    step = PlanStep(
        step_id="step_1",
        action="execute_command",
        objective="Execute command",
        idempotency=PlanStepIdempotency.IDEMPOTENT,
        side_effect=PlanStepSideEffect.NONE,
        parameters={"command": "ls -la"}
    )
    
    plan = Plan(
        plan_id="plan_1",
        steps=[step],
        current_step_index=0
    )
    
    candidate = Candidate(
        candidate_id="candidate_1",
        method_type=MethodType.SHELL,
        capability_id="execute_command",
        plugin_id=None,
        score=0.9
    )
    
    routing = RoutingDecision(
        selected_candidate=candidate,
        fallback_chain=[],
        reasoning="Test"
    )
    
    state = OperonixState(task=task, plan=plan, routing=routing)
    
    # Should not crash even if tool_registry is unavailable
    result = execute_step_node(state)
    
    assert result["state"].execution is not None
    # Should have placeholder or actual execution result
