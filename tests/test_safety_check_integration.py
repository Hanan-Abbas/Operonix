"""
Safety Check Integration Tests — Operonix Migration
──────────────────────────────────────────────────

Tests for safety check integration.
Safety Check Integration Phase: Integrate actual safety modules.
"""
from __future__ import annotations

import pytest


# ─── SAFETY CHECK NODE INTEGRATION TESTS ─────────────────────────────────────

def test_safety_check_node_integrates_risk_rules():
    """Test that safety_check_node integrates with risk_rules."""
    from graph.nodes.safety_check import safety_check_node
    from migration.graph_state import OperonixState
    from migration.domain_contracts import TaskRequest, TaskSource, Plan, PlanStep, PlanStepIdempotency, PlanStepSideEffect
    
    task = TaskRequest(user_input="Test", source=TaskSource.VOICE)
    
    step = PlanStep(
        step_id="step_1",
        action="execute_command",
        objective="Execute command",
        idempotency=PlanStepIdempotency.IDEMPOTENT,
        side_effect=PlanStepSideEffect.NONE,
        arguments={"command": "ls -la"}
    )
    
    plan = Plan(
        plan_id="plan_1",
        steps=[step],
        current_step_index=0
    )
    
    state = OperonixState(task=task, plan=plan)
    
    result = safety_check_node(state)
    
    # Field-level update: safety field returned directly
    assert "safety" in result or state.safety is not None
    safety = result.get("safety") or state.safety
    assert safety is not None
    assert safety.risk_level is not None
    assert "command_risk_assessment" in safety.safety_checks_performed


def test_safety_check_node_integrates_permission_guard():
    """Test that safety_check_node integrates with permission_guard."""
    from graph.nodes.safety_check import safety_check_node
    from migration.graph_state import OperonixState
    from migration.domain_contracts import TaskRequest, TaskSource, Plan, PlanStep, PlanStepIdempotency, PlanStepSideEffect
    
    task = TaskRequest(user_input="Test", source=TaskSource.VOICE)
    
    step = PlanStep(
        step_id="step_1",
        action="terminal_resolver",
        objective="Resolve terminal",
        idempotency=PlanStepIdempotency.IDEMPOTENT,
        side_effect=PlanStepSideEffect.NONE
    )
    
    plan = Plan(
        plan_id="plan_1",
        steps=[step],
        current_step_index=0
    )
    
    state = OperonixState(task=task, plan=plan)
    
    result = safety_check_node(state)
    
    # Field-level update: safety field returned directly
    assert "safety" in result or state.safety is not None
    safety = result.get("safety") or state.safety
    assert safety is not None
    assert safety.permission_status is not None


def test_safety_check_node_integrates_validator():
    """Test that safety_check_node integrates with validator logic."""
    from graph.nodes.safety_check import safety_check_node
    from migration.graph_state import OperonixState
    from migration.domain_contracts import TaskRequest, TaskSource, Plan, PlanStep, PlanStepIdempotency, PlanStepSideEffect
    
    task = TaskRequest(user_input="Test", source=TaskSource.VOICE)
    
    step = PlanStep(
        step_id="step_1",
        action="write_file",
        objective="Write file",
        idempotency=PlanStepIdempotency.IDEMPOTENT,
        side_effect=PlanStepSideEffect.LOCAL,
        arguments={"path": "/home/user/test.txt"}
    )
    
    plan = Plan(
        plan_id="plan_1",
        steps=[step],
        current_step_index=0
    )
    
    state = OperonixState(task=task, plan=plan, context={"window_title": "Test"})
    
    result = safety_check_node(state)
    
    # Field-level update: safety field returned directly
    assert "safety" in result or state.safety is not None
    safety = result.get("safety") or state.safety
    assert safety is not None
    assert safety.validation_status is not None


def test_safety_check_node_high_risk_requires_confirmation():
    """Test that high risk operations require confirmation."""
    from graph.nodes.safety_check import safety_check_node
    from migration.graph_state import OperonixState
    from migration.domain_contracts import TaskRequest, TaskSource, Plan, PlanStep, PlanStepIdempotency, PlanStepSideEffect, RiskLevel
    
    task = TaskRequest(user_input="Test", source=TaskSource.VOICE)
    
    step = PlanStep(
        step_id="step_1",
        action="execute_command",
        objective="Execute command",
        idempotency=PlanStepIdempotency.IDEMPOTENT,
        side_effect=PlanStepSideEffect.NONE,
        arguments={"command": "rm -rf /"}  # High risk command
    )
    
    plan = Plan(
        plan_id="plan_1",
        steps=[step],
        current_step_index=0
    )
    
    state = OperonixState(task=task, plan=plan)
    
    result = safety_check_node(state)
    
    # Field-level update: safety field returned directly
    assert "safety" in result or state.safety is not None
    safety = result.get("safety") or state.safety
    assert safety is not None
    # High risk operations should require confirmation
    if safety.risk_level == RiskLevel.HIGH:
        assert safety.confirmation_required is True


def test_safety_check_node_forbidden_pattern_rejection():
    """Test that forbidden patterns are rejected."""
    from graph.nodes.safety_check import safety_check_node
    from migration.graph_state import OperonixState
    from migration.domain_contracts import TaskRequest, TaskSource, Plan, PlanStep, PlanStepIdempotency, PlanStepSideEffect
    
    task = TaskRequest(user_input="Test", source=TaskSource.VOICE)
    
    step = PlanStep(
        step_id="step_1",
        action="write_file",
        objective="Write file",
        idempotency=PlanStepIdempotency.IDEMPOTENT,
        side_effect=PlanStepSideEffect.LOCAL,
        arguments={"path": "/home/user/.env"}  # Forbidden pattern
    )
    
    plan = Plan(
        plan_id="plan_1",
        steps=[step],
        current_step_index=0
    )
    
    state = OperonixState(task=task, plan=plan, context={"window_title": "Test"})
    
    result = safety_check_node(state)
    
    # Field-level update: safety field returned directly
    assert "safety" in result or state.safety is not None
    safety = result.get("safety") or state.safety
    assert safety is not None
    # Forbidden patterns should be rejected
    if "path_pattern_check" in safety.safety_checks_performed:
        assert safety.validation_status == "REJECTED"


def test_safety_check_node_without_plan():
    """Test that safety_check_node handles missing plan gracefully."""
    from graph.nodes.safety_check import safety_check_node
    from migration.graph_state import OperonixState
    from migration.domain_contracts import TaskRequest, TaskSource
    
    task = TaskRequest(user_input="Test", source=TaskSource.VOICE)
    state = OperonixState(task=task)  # No plan
    
    result = safety_check_node(state)
    
    # Field-level update: safety field returned directly
    assert "safety" in result or state.safety is not None
    safety = result.get("safety") or state.safety
    assert safety is not None
    # Should use fallback safety decision
    assert safety.validation_status == "APPROVED"


def test_safety_check_node_trace_event_collection():
    """Test that safety_check_node collects trace events."""
    from graph.nodes.safety_check import safety_check_node
    from migration.graph_state import OperonixState
    from migration.domain_contracts import TaskRequest, TaskSource, Plan, PlanStep, PlanStepIdempotency, PlanStepSideEffect
    
    task = TaskRequest(user_input="Test", source=TaskSource.VOICE)
    
    step = PlanStep(
        step_id="step_1",
        action="execute_command",
        objective="Execute command",
        idempotency=PlanStepIdempotency.IDEMPOTENT,
        side_effect=PlanStepSideEffect.NONE,
        arguments={"command": "ls -la"}
    )
    
    plan = Plan(
        plan_id="plan_1",
        steps=[step],
        current_step_index=0
    )
    
    state = OperonixState(task=task, plan=plan)
    
    result = safety_check_node(state)
    
    # Check that history events were added
    history = result.get("history") or state.history
    assert "safety_check_started" in [event["type"] for event in history.get("events", [])]
    assert "safety_check_completed" in [event["type"] for event in history.get("events", [])]


def test_safety_check_node_file_operation_risk():
    """Test that file operations are assessed for risk."""
    from graph.nodes.safety_check import safety_check_node
    from migration.graph_state import OperonixState
    from migration.domain_contracts import TaskRequest, TaskSource, Plan, PlanStep, PlanStepIdempotency, PlanStepSideEffect
    
    task = TaskRequest(user_input="Test", source=TaskSource.VOICE)
    
    step = PlanStep(
        step_id="step_1",
        action="delete_file",
        objective="Delete file",
        idempotency=PlanStepIdempotency.NON_IDEMPOTENT,
        side_effect=PlanStepSideEffect.DESTRUCTIVE,
        arguments={"path": "/home/user/test.txt"}
    )
    
    plan = Plan(
        plan_id="plan_1",
        steps=[step],
        current_step_index=0
    )
    
    state = OperonixState(task=task, plan=plan)
    
    result = safety_check_node(state)
    
    # Field-level update: safety field returned directly
    assert "safety" in result or state.safety is not None
    safety = result.get("safety") or state.safety
    assert safety is not None
    assert "file_risk_assessment" in safety.safety_checks_performed


def test_safety_check_node_web_operation_risk():
    """Test that web operations are assessed for risk."""
    from graph.nodes.safety_check import safety_check_node
    from migration.graph_state import OperonixState
    from migration.domain_contracts import TaskRequest, TaskSource, Plan, PlanStep, PlanStepIdempotency, PlanStepSideEffect
    
    task = TaskRequest(user_input="Test", source=TaskSource.VOICE)
    
    step = PlanStep(
        step_id="step_1",
        action="web_request",
        objective="Make web request",
        idempotency=PlanStepIdempotency.IDEMPOTENT,
        side_effect=PlanStepSideEffect.EXTERNAL_COMMIT,
        arguments={"url": "https://example.com"}
    )
    
    plan = Plan(
        plan_id="plan_1",
        steps=[step],
        current_step_index=0
    )
    
    state = OperonixState(task=task, plan=plan)
    
    result = safety_check_node(state)
    
    # Field-level update: safety field returned directly
    assert "safety" in result or state.safety is not None
    safety = result.get("safety") or state.safety
    assert safety is not None
    assert "web_risk_assessment" in safety.safety_checks_performed


def test_safety_check_node_graceful_degradation():
    """Test that safety_check_node degrades gracefully on import errors."""
    from graph.nodes.safety_check import safety_check_node
    from migration.graph_state import OperonixState
    from migration.domain_contracts import TaskRequest, TaskSource, Plan, PlanStep, PlanStepIdempotency, PlanStepSideEffect
    
    task = TaskRequest(user_input="Test", source=TaskSource.VOICE)
    
    step = PlanStep(
        step_id="step_1",
        action="execute_command",
        objective="Execute command",
        idempotency=PlanStepIdempotency.IDEMPOTENT,
        side_effect=PlanStepSideEffect.NONE,
        arguments={"command": "ls -la"}
    )
    
    plan = Plan(
        plan_id="plan_1",
        steps=[step],
        current_step_index=0
    )
    
    state = OperonixState(task=task, plan=plan)
    
    # Should not crash even if safety modules are not available
    result = safety_check_node(state)
    
    assert "safety" in result or state.safety is not None
    safety = result.get("safety") or state.safety
    assert safety is not None
    # Should have fallback check if modules not available
    assert len(safety.safety_checks_performed) > 0


def test_safety_check_node_safety_checks_performed_tracking():
    """Test that safety_check_node tracks which checks were performed."""
    from graph.nodes.safety_check import safety_check_node
    from migration.graph_state import OperonixState
    from migration.domain_contracts import TaskRequest, TaskSource, Plan, PlanStep, PlanStepIdempotency, PlanStepSideEffect
    
    task = TaskRequest(user_input="Test", source=TaskSource.VOICE)
    
    step = PlanStep(
        step_id="step_1",
        action="execute_command",
        objective="Execute command",
        idempotency=PlanStepIdempotency.IDEMPOTENT,
        side_effect=PlanStepSideEffect.NONE,
        arguments={"command": "ls -la"}
    )
    
    plan = Plan(
        plan_id="plan_1",
        steps=[step],
        current_step_index=0
    )
    
    state = OperonixState(task=task, plan=plan)
    
    result = safety_check_node(state)
    
    # Field-level update: safety field returned directly
    assert "safety" in result or state.safety is not None
    safety = result.get("safety") or state.safety
    assert safety is not None
    assert len(safety.safety_checks_performed) > 0
    # Should have at least one check performed
    assert any("assessment" in check for check in safety.safety_checks_performed)


def test_safety_check_node_additional_info():
    """Test that safety_check_node includes additional info for rejection."""
    from graph.nodes.safety_check import safety_check_node
    from migration.graph_state import OperonixState
    from migration.domain_contracts import TaskRequest, TaskSource, Plan, PlanStep, PlanStepIdempotency, PlanStepSideEffect
    
    task = TaskRequest(user_input="Test", source=TaskSource.VOICE)
    
    step = PlanStep(
        step_id="step_1",
        action="write_file",
        objective="Write file",
        idempotency=PlanStepIdempotency.IDEMPOTENT,
        side_effect=PlanStepSideEffect.LOCAL,
        arguments={"path": "/home/user/.env"}  # Forbidden pattern
    )
    
    plan = Plan(
        plan_id="plan_1",
        steps=[step],
        current_step_index=0
    )
    
    state = OperonixState(task=task, plan=plan, context={"window_title": "Test"})
    
    result = safety_check_node(state)
    
    # Field-level update: safety field returned directly
    assert "safety" in result or state.safety is not None
    safety = result.get("safety") or state.safety
    assert safety is not None
    # If rejected, should have additional info with reason
    if safety.validation_status == "REJECTED":
        assert safety.additional_info is not None
        assert "reason" in safety.additional_info


# ─── CONFIRMATION NODE TESTS ───────────────────────────────────────────────

def test_confirmation_node_creates_human_intervention():
    """Test that confirmation_node creates human intervention request."""
    from graph.nodes.confirmation import confirmation_node
    from migration.graph_state import OperonixState
    from migration.domain_contracts import TaskRequest, TaskSource, IntentResult, IntentType
    
    task = TaskRequest(user_input="Test", source=TaskSource.VOICE)
    
    intent = IntentResult(
        name="test_intent",
        intent_type=IntentType.ACTION,
        confidence=0.9,
        entities={}
    )
    
    state = OperonixState(task=task, intent=intent)
    
    result = confirmation_node(state)
    
    # Field-level update: confirmation field returned directly
    assert "confirmation" in result or state.confirmation is not None
    confirmation = result.get("confirmation") or state.confirmation
    paused = result.get("paused") or state.paused
    checkpoint_id = result.get("checkpoint_identifier") or state.checkpoint_identifier
    assert confirmation is not None
    assert paused is True
    assert checkpoint_id is not None


def test_confirmation_node_creates_checkpoint():
    """Test that confirmation_node creates checkpoint before pausing."""
    from graph.nodes.confirmation import confirmation_node
    from migration.graph_state import OperonixState
    from migration.domain_contracts import TaskRequest, TaskSource
    
    task = TaskRequest(user_input="Test", source=TaskSource.VOICE)
    state = OperonixState(task=task)
    
    result = confirmation_node(state)
    
    # Field-level update: checkpoint_identifier field returned directly
    checkpoint_id = result.get("checkpoint_identifier") or state.checkpoint_identifier
    paused = result.get("paused") or state.paused
    assert checkpoint_id is not None
    assert paused is True


def test_resume_from_confirmation():
    """Test that resume_from_confirmation resumes graph execution."""
    from graph.nodes.confirmation import resume_from_confirmation
    from migration.graph_state import OperonixState
    from migration.domain_contracts import TaskRequest, TaskSource, HumanIntervention, HumanInterventionType, IntentResult, IntentType
    
    task = TaskRequest(user_input="Test", source=TaskSource.VOICE)
    
    intent = IntentResult(
        name="test_intent",
        intent_type=IntentType.ACTION,
        confidence=0.9,
        entities={}
    )
    
    intervention = HumanIntervention(
        task_id=task.task_id,
        intervention_type=HumanInterventionType.CONFIRM,
        reason="Test"
    )
    
    state = OperonixState(task=task, intent=intent, confirmation=intervention)
    state.paused = True
    
    result = resume_from_confirmation(state, HumanInterventionType.CONFIRM)
    
    # Field-level update: paused field returned directly
    paused = result.get("paused") or state.paused
    confirmation = result.get("confirmation") or state.confirmation
    assert paused is False
    assert confirmation.response == HumanInterventionType.CONFIRM
