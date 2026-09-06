"""
Idempotency & Safe Re-execution Tests — Operonix Migration Phase 6
────────────────────────────────────────────────────────────────────

Tests for idempotency, side-effect awareness, and safe re-execution.
Per migration plan Phase 6: Idempotency, Side Effects & Safe Re-execution
"""
from __future__ import annotations

import pytest


# ─── IDEMPOTENCY AWARE RETRY TESTS ───────────────────────────────────────────

def test_idempotent_operation_safe_to_retry():
    """Test that idempotent operations are safe to retry."""
    from graph.nodes.recover import _is_safe_to_retry
    from migration.graph_state import OperonixState
    from migration.domain_contracts import TaskRequest, TaskSource, Plan, PlanStep
    
    task = TaskRequest(user_input="Open Firefox", source=TaskSource.VOICE)
    state = OperonixState(task=task)
    
    # Create plan with idempotent step
    step = PlanStep(
        step_id="step_1",
        action="execute_intent",
        arguments={},
        objective="Open Firefox",
        idempotency="SAFE",
        side_effect="READ_ONLY",
        reversibility=True
    )
    state.plan = Plan(steps=[step])
    state.plan.current_step_index = 0
    
    is_safe = _is_safe_to_retry(state)
    assert is_safe is True


def test_non_idempotent_operation_not_safe_to_retry():
    """Test that non-idempotent operations are not safe to retry."""
    from graph.nodes.recover import _is_safe_to_retry
    from migration.graph_state import OperonixState
    from migration.domain_contracts import TaskRequest, TaskSource, Plan, PlanStep
    
    task = TaskRequest(user_input="Delete file", source=TaskSource.VOICE)
    state = OperonixState(task=task)
    
    # Create plan with non-idempotent step
    step = PlanStep(
        step_id="step_1",
        action="execute_intent",
        arguments={},
        objective="Delete file",
        idempotency="NON_IDEMPOTENT",
        side_effect="DESTRUCTIVE",
        reversibility=False
    )
    state.plan = Plan(steps=[step])
    state.plan.current_step_index = 0
    
    is_safe = _is_safe_to_retry(state)
    assert is_safe is False


def test_destructive_side_effect_not_safe_to_retry():
    """Test that DESTRUCTIVE side-effect operations are not safe to retry."""
    from graph.nodes.recover import _is_safe_to_retry
    from migration.graph_state import OperonixState
    from migration.domain_contracts import TaskRequest, TaskSource, Plan, PlanStep
    
    task = TaskRequest(user_input="Delete file", source=TaskSource.VOICE)
    state = OperonixState(task=task)
    
    # Create plan with DESTRUCTIVE side-effect
    step = PlanStep(
        step_id="step_1",
        action="execute_intent",
        arguments={},
        objective="Delete file",
        idempotency="CONDITIONAL",
        side_effect="DESTRUCTIVE",
        reversibility=False
    )
    state.plan = Plan(steps=[step])
    state.plan.current_step_index = 0
    
    is_safe = _is_safe_to_retry(state)
    assert is_safe is False


def test_external_commit_side_effect_not_safe_to_retry():
    """Test that EXTERNAL_COMMIT side-effect operations are not safe to retry."""
    from graph.nodes.recover import _is_safe_to_retry
    from migration.graph_state import OperonixState
    from migration.domain_contracts import TaskRequest, TaskSource, Plan, PlanStep
    
    task = TaskRequest(user_input="Send email", source=TaskSource.VOICE)
    state = OperonixState(task=task)
    
    # Create plan with EXTERNAL_COMMIT side-effect
    step = PlanStep(
        step_id="step_1",
        action="execute_intent",
        arguments={},
        objective="Send email",
        idempotency="CONDITIONAL",
        side_effect="EXTERNAL_COMMIT",
        reversibility=False
    )
    state.plan = Plan(steps=[step])
    state.plan.current_step_index = 0
    
    is_safe = _is_safe_to_retry(state)
    assert is_safe is False


def test_limited_side_effect_safe_to_retry():
    """Test that LIMITED_SIDE_EFFECT operations are safe to retry."""
    from graph.nodes.recover import _is_safe_to_retry
    from migration.graph_state import OperonixState
    from migration.domain_contracts import TaskRequest, TaskSource, Plan, PlanStep
    
    task = TaskRequest(user_input="Create file", source=TaskSource.VOICE)
    state = OperonixState(task=task)
    
    # Create plan with LIMITED_SIDE_EFFECT
    step = PlanStep(
        step_id="step_1",
        action="execute_intent",
        arguments={},
        objective="Create file",
        idempotency="CONDITIONAL",
        side_effect="LIMITED_SIDE_EFFECT",
        reversibility=True
    )
    state.plan = Plan(steps=[step])
    state.plan.current_step_index = 0
    
    is_safe = _is_safe_to_retry(state)
    assert is_safe is True


def test_non_reversible_operation_caution():
    """Test that non-reversible operations are logged with caution but may still retry."""
    from graph.nodes.recover import _is_safe_to_retry
    from migration.graph_state import OperonixState
    from migration.domain_contracts import TaskRequest, TaskSource, Plan, PlanStep
    
    task = TaskRequest(user_input="Modify file", source=TaskSource.VOICE)
    state = OperonixState(task=task)
    
    # Create plan with non-reversible but safe side-effect
    step = PlanStep(
        step_id="step_1",
        action="execute_intent",
        arguments={},
        objective="Modify file",
        idempotency="CONDITIONAL",
        side_effect="LIMITED_SIDE_EFFECT",
        reversibility=False
    )
    state.plan = Plan(steps=[step])
    state.plan.current_step_index = 0
    
    is_safe = _is_safe_to_retry(state)
    # Non-reversible but still safe to retry (with caution)
    assert is_safe is True


def test_no_plan_not_safe_to_retry():
    """Test that missing plan results in not safe to retry."""
    from graph.nodes.recover import _is_safe_to_retry
    from migration.graph_state import OperonixState
    from migration.domain_contracts import TaskRequest, TaskSource
    
    task = TaskRequest(user_input="Open Firefox", source=TaskSource.VOICE)
    state = OperonixState(task=task)
    
    is_safe = _is_safe_to_retry(state)
    assert is_safe is False


def test_invalid_step_index_not_safe_to_retry():
    """Test that invalid step index results in not safe to retry."""
    from graph.nodes.recover import _is_safe_to_retry
    from migration.graph_state import OperonixState
    from migration.domain_contracts import TaskRequest, TaskSource, Plan, PlanStep
    
    task = TaskRequest(user_input="Open Firefox", source=TaskSource.VOICE)
    state = OperonixState(task=task)
    
    # Create plan with one step
    step = PlanStep(
        step_id="step_1",
        action="execute_intent",
        arguments={},
        objective="Open Firefox",
        idempotency="SAFE",
        side_effect="READ_ONLY",
        reversibility=True
    )
    state.plan = Plan(steps=[step])
    state.plan.current_step_index = 5  # Invalid index
    
    is_safe = _is_safe_to_retry(state)
    assert is_safe is False


# ─── UNCERTAIN_OUTCOME TESTS ───────────────────────────────────────────────────

def test_uncertain_outcome_status_in_verification_result():
    """Test that UNCERTAIN_OUTCOME is a valid verification status."""
    from migration.domain_contracts import VerificationResult, ContextSnapshot
    
    verification_result = VerificationResult(
        status="UNCERTAIN_OUTCOME",
        observed_context=ContextSnapshot(),
        expected_state={},
        actual_state={},
        reason="Non-idempotent operation failed, outcome uncertain"
    )
    
    assert verification_result.status == "UNCERTAIN_OUTCOME"


def test_non_idempotent_failure_triggers_uncertain_outcome():
    """Test that non-idempotent operation failure triggers UNCERTAIN_OUTCOME."""
    from graph.nodes.verify_step import _verify_postconditions
    from migration.graph_state import OperonixState
    from migration.domain_contracts import TaskRequest, TaskSource, Plan, PlanStep, ExecutionResult, TaskStatus
    
    task = TaskRequest(user_input="Delete file", source=TaskSource.VOICE)
    state = OperonixState(task=task)
    
    # Create plan with non-idempotent step
    step = PlanStep(
        step_id="step_1",
        action="execute_intent",
        arguments={},
        objective="Delete file",
        idempotency="NON_IDEMPOTENT",
        side_effect="DESTRUCTIVE",
        reversibility=False
    )
    state.plan = Plan(steps=[step])
    state.plan.current_step_index = 0
    
    # Execution failed
    state.execution = ExecutionResult(
        step_id="step_1",
        execution_status=TaskStatus.FAILED,
        result={"error": "Command failed"}
    )
    
    verification_result = _verify_postconditions(state)
    assert verification_result.status == "UNCERTAIN_OUTCOME"
    assert "outcome uncertain" in verification_result.reason


def test_destructive_side_effect_failure_triggers_uncertain_outcome():
    """Test that DESTRUCTIVE side-effect operation failure triggers UNCERTAIN_OUTCOME."""
    from graph.nodes.verify_step import _verify_postconditions
    from migration.graph_state import OperonixState
    from migration.domain_contracts import TaskRequest, TaskSource, Plan, PlanStep, ExecutionResult, TaskStatus
    
    task = TaskRequest(user_input="Delete file", source=TaskSource.VOICE)
    state = OperonixState(task=task)
    
    # Create plan with DESTRUCTIVE side-effect
    step = PlanStep(
        step_id="step_1",
        action="execute_intent",
        arguments={},
        objective="Delete file",
        idempotency="CONDITIONAL",
        side_effect="DESTRUCTIVE",
        reversibility=False
    )
    state.plan = Plan(steps=[step])
    state.plan.current_step_index = 0
    
    # Execution failed
    state.execution = ExecutionResult(
        step_id="step_1",
        execution_status=TaskStatus.FAILED,
        result={"error": "Command failed"}
    )
    
    verification_result = _verify_postconditions(state)
    assert verification_result.status == "UNCERTAIN_OUTCOME"
    assert "outcome uncertain" in verification_result.reason


def test_external_commit_failure_triggers_uncertain_outcome():
    """Test that EXTERNAL_COMMIT side-effect operation failure triggers UNCERTAIN_OUTCOME."""
    from graph.nodes.verify_step import _verify_postconditions
    from migration.graph_state import OperonixState
    from migration.domain_contracts import TaskRequest, TaskSource, Plan, PlanStep, ExecutionResult, TaskStatus
    
    task = TaskRequest(user_input="Send email", source=TaskSource.VOICE)
    state = OperonixState(task=task)
    
    # Create plan with EXTERNAL_COMMIT side-effect
    step = PlanStep(
        step_id="step_1",
        action="execute_intent",
        arguments={},
        objective="Send email",
        idempotency="CONDITIONAL",
        side_effect="EXTERNAL_COMMIT",
        reversibility=False
    )
    state.plan = Plan(steps=[step])
    state.plan.current_step_index = 0
    
    # Execution failed
    state.execution = ExecutionResult(
        step_id="step_1",
        execution_status=TaskStatus.FAILED,
        result={"error": "SMTP error"}
    )
    
    verification_result = _verify_postconditions(state)
    assert verification_result.status == "UNCERTAIN_OUTCOME"
    assert "outcome uncertain" in verification_result.reason


def test_idempotent_operation_failure_does_not_trigger_uncertain_outcome():
    """Test that idempotent operation failure does not trigger UNCERTAIN_OUTCOME."""
    from graph.nodes.verify_step import _verify_postconditions
    from migration.graph_state import OperonixState
    from migration.domain_contracts import TaskRequest, TaskSource, Plan, PlanStep, ExecutionResult, TaskStatus
    
    task = TaskRequest(user_input="Open Firefox", source=TaskSource.VOICE)
    state = OperonixState(task=task)
    
    # Create plan with idempotent step
    step = PlanStep(
        step_id="step_1",
        action="execute_intent",
        arguments={},
        objective="Open Firefox",
        idempotency="SAFE",
        side_effect="READ_ONLY",
        reversibility=True
    )
    state.plan = Plan(steps=[step])
    state.plan.current_step_index = 0
    
    # Execution failed
    state.execution = ExecutionResult(
        step_id="step_1",
        execution_status=TaskStatus.FAILED,
        result={"error": "Command failed"}
    )
    
    verification_result = _verify_postconditions(state)
    # Should not be UNCERTAIN_OUTCOME for idempotent operations
    assert verification_result.status != "UNCERTAIN_OUTCOME"


# ─── POSTCONDITION CHECK TESTS ───────────────────────────────────────────────────

def test_postcondition_check_function():
    """Test that _check_postconditions function exists and returns bool."""
    from graph.nodes.observe import _check_postconditions
    from migration.graph_state import OperonixState
    from migration.domain_contracts import TaskRequest, TaskSource
    
    task = TaskRequest(user_input="Open Firefox", source=TaskSource.VOICE)
    state = OperonixState(task=task)
    
    result = _check_postconditions(state)
    assert isinstance(result, bool)


def test_postcondition_check_with_verified_verification():
    """Test that postcondition check returns True if verification is VERIFIED."""
    from graph.nodes.observe import _check_postconditions
    from migration.graph_state import OperonixState
    from migration.domain_contracts import TaskRequest, TaskSource, Plan, PlanStep, VerificationResult, ContextSnapshot
    
    task = TaskRequest(user_input="Open Firefox", source=TaskSource.VOICE)
    state = OperonixState(task=task)
    
    # Create plan
    step = PlanStep(
        step_id="step_1",
        action="execute_intent",
        arguments={},
        objective="Open Firefox",
        idempotency="SAFE",
        side_effect="READ_ONLY",
        reversibility=True
    )
    state.plan = Plan(steps=[step])
    state.plan.current_step_index = 0
    
    # Verification already verified
    state.verification = VerificationResult(
        status="VERIFIED",
        observed_context=ContextSnapshot(),
        expected_state={},
        actual_state={},
        reason="Postconditions verified"
    )
    
    result = _check_postconditions(state)
    assert result is True


def test_postcondition_check_without_plan():
    """Test that postcondition check returns False without plan."""
    from graph.nodes.observe import _check_postconditions
    from migration.graph_state import OperonixState
    from migration.domain_contracts import TaskRequest, TaskSource
    
    task = TaskRequest(user_input="Open Firefox", source=TaskSource.VOICE)
    state = OperonixState(task=task)
    
    result = _check_postconditions(state)
    assert result is False


def test_observe_node_recovery_observation():
    """Test that observe node handles recovery observation."""
    from graph.nodes.observe import observe_node
    from migration.graph_state import OperonixState
    from migration.domain_contracts import TaskRequest, TaskSource, RecoveryDecision, FailureCategory, RecoveryStrategy
    
    task = TaskRequest(user_input="Open Firefox", source=TaskSource.VOICE)
    state = OperonixState(task=task)
    
    # Set recovery with observe strategy
    state.recovery = RecoveryDecision(
        failure_category=FailureCategory.CONTEXT_MISMATCH,
        recovery_strategy=RecoveryStrategy.OBSERVE,
        retry_count=1,
        target_stage="observe",
        reason="Context mismatch, observing to check postconditions"
    )
    
    result = observe_node(state)
    
    # Should have postcondition check in context
    assert result["state"].context is not None
    assert "postcondition_check" in result["state"].context


def test_observe_node_initial_observation():
    """Test that observe node handles initial observation (not recovery)."""
    from graph.nodes.observe import observe_node
    from migration.graph_state import OperonixState
    from migration.domain_contracts import TaskRequest, TaskSource
    
    task = TaskRequest(user_input="Open Firefox", source=TaskSource.VOICE)
    state = OperonixState(task=task)
    
    result = observe_node(state)
    
    # Should not have postcondition check (not recovery)
    assert result["state"].context is not None
    # postcondition_check may or may not be present, but should not be set


# ─── RECOVERY STRATEGY WITH IDEMPOTENCY TESTS ────────────────────────────────────

def test_transient_failure_with_idempotent_step_retries():
    """Test that transient failure with idempotent step triggers retry."""
    from graph.nodes.recover import _determine_recovery_strategy
    from migration.graph_state import OperonixState
    from migration.domain_contracts import TaskRequest, TaskSource, Plan, PlanStep, FailureCategory
    
    task = TaskRequest(user_input="Open Firefox", source=TaskSource.VOICE)
    state = OperonixState(task=task)
    
    # Create plan with idempotent step
    step = PlanStep(
        step_id="step_1",
        action="execute_intent",
        arguments={},
        objective="Open Firefox",
        idempotency="SAFE",
        side_effect="READ_ONLY",
        reversibility=True
    )
    state.plan = Plan(steps=[step])
    state.plan.current_step_index = 0
    
    strategy = _determine_recovery_strategy(FailureCategory.TRANSIENT, state)
    assert strategy.value == "retry"


def test_transient_failure_with_non_idempotent_step_observes():
    """Test that transient failure with non-idempotent step triggers observe."""
    from graph.nodes.recover import _determine_recovery_strategy
    from migration.graph_state import OperonixState
    from migration.domain_contracts import TaskRequest, TaskSource, Plan, PlanStep, FailureCategory
    
    task = TaskRequest(user_input="Delete file", source=TaskSource.VOICE)
    state = OperonixState(task=task)
    
    # Create plan with non-idempotent step
    step = PlanStep(
        step_id="step_1",
        action="execute_intent",
        arguments={},
        objective="Delete file",
        idempotency="NON_IDEMPOTENT",
        side_effect="DESTRUCTIVE",
        reversibility=False
    )
    state.plan = Plan(steps=[step])
    state.plan.current_step_index = 0
    
    strategy = _determine_recovery_strategy(FailureCategory.TRANSIENT, state)
    assert strategy.value == "observe"


def test_transient_failure_with_destructive_side_effect_observes():
    """Test that transient failure with DESTRUCTIVE side-effect triggers observe."""
    from graph.nodes.recover import _determine_recovery_strategy
    from migration.graph_state import OperonixState
    from migration.domain_contracts import TaskRequest, TaskSource, Plan, PlanStep, FailureCategory
    
    task = TaskRequest(user_input="Delete file", source=TaskSource.VOICE)
    state = OperonixState(task=task)
    
    # Create plan with DESTRUCTIVE side-effect
    step = PlanStep(
        step_id="step_1",
        action="execute_intent",
        arguments={},
        objective="Delete file",
        idempotency="CONDITIONAL",
        side_effect="DESTRUCTIVE",
        reversibility=False
    )
    state.plan = Plan(steps=[step])
    state.plan.current_step_index = 0
    
    strategy = _determine_recovery_strategy(FailureCategory.TRANSIENT, state)
    assert strategy.value == "observe"


def test_transient_failure_with_external_commit_observes():
    """Test that transient failure with EXTERNAL_COMMIT triggers observe."""
    from graph.nodes.recover import _determine_recovery_strategy
    from migration.graph_state import OperonixState
    from migration.domain_contracts import TaskRequest, TaskSource, Plan, PlanStep, FailureCategory
    
    task = TaskRequest(user_input="Send email", source=TaskSource.VOICE)
    state = OperonixState(task=task)
    
    # Create plan with EXTERNAL_COMMIT side-effect
    step = PlanStep(
        step_id="step_1",
        action="execute_intent",
        arguments={},
        objective="Send email",
        idempotency="CONDITIONAL",
        side_effect="EXTERNAL_COMMIT",
        reversibility=False
    )
    state.plan = Plan(steps=[step])
    state.plan.current_step_index = 0
    
    strategy = _determine_recovery_strategy(FailureCategory.TRANSIENT, state)
    assert strategy.value == "observe"


# ─── GRAPH CONDITIONAL ROUTING TESTS ─────────────────────────────────────────────

def test_graph_conditional_routing_uncertain_outcome():
    """Test that UNCERTAIN_OUTCOME triggers recovery (observe)."""
    from graph.graph import should_recover
    from migration.graph_state import OperonixState
    from migration.domain_contracts import TaskRequest, TaskSource, VerificationResult, ContextSnapshot
    
    task = TaskRequest(user_input="Delete file", source=TaskSource.VOICE)
    state = OperonixState(task=task)
    
    state.verification = VerificationResult(
        status="UNCERTAIN_OUTCOME",
        observed_context=ContextSnapshot(),
        expected_state={},
        actual_state={},
        reason="Non-idempotent operation failed, outcome uncertain"
    )
    
    result = should_recover(state)
    assert result == "recover"


def test_graph_conditional_routing_verified():
    """Test that VERIFIED status triggers finalize."""
    from graph.graph import should_recover
    from migration.graph_state import OperonixState
    from migration.domain_contracts import TaskRequest, TaskSource, VerificationResult, ContextSnapshot
    
    task = TaskRequest(user_input="Open Firefox", source=TaskSource.VOICE)
    state = OperonixState(task=task)
    
    state.verification = VerificationResult(
        status="VERIFIED",
        observed_context=ContextSnapshot(),
        expected_state={},
        actual_state={},
        reason="Postconditions verified"
    )
    
    result = should_recover(state)
    assert result == "finalize"


def test_graph_conditional_routing_failed():
    """Test that FAILED status triggers recovery."""
    from graph.graph import should_recover
    from migration.graph_state import OperonixState
    from migration.domain_contracts import TaskRequest, TaskSource, VerificationResult, ContextSnapshot
    
    task = TaskRequest(user_input="Open Firefox", source=TaskSource.VOICE)
    state = OperonixState(task=task)
    
    state.verification = VerificationResult(
        status="FAILED",
        observed_context=ContextSnapshot(),
        expected_state={},
        actual_state={},
        reason="Execution failed"
    )
    
    result = should_recover(state)
    assert result == "recover"


def test_graph_conditional_routing_uncertain():
    """Test that UNCERTAIN status triggers recovery."""
    from graph.graph import should_recover
    from migration.graph_state import OperonixState
    from migration.domain_contracts import TaskRequest, TaskSource, VerificationResult, ContextSnapshot
    
    task = TaskRequest(user_input="Open Firefox", source=TaskSource.VOICE)
    state = OperonixState(task=task)
    
    state.verification = VerificationResult(
        status="UNCERTAIN",
        observed_context=ContextSnapshot(),
        expected_state={},
        actual_state={},
        reason="No execution result"
    )
    
    result = should_recover(state)
    assert result == "recover"
