"""
Verification & Recovery Integration Tests — Operonix Migration Phase 5
────────────────────────────────────────────────────────────────────────

Tests for verification logic and recovery mechanisms.
Per migration plan Phase 5: Verification & Recovery
"""
from __future__ import annotations

import pytest
from datetime import datetime


# ─── VERIFICATION TESTS ─────────────────────────────────────────────────────

def test_verify_step_node_import():
    """Test that verify_step node can be imported."""
    from graph.nodes.verify_step import verify_step_node
    assert verify_step_node is not None


def test_verify_step_executor_success_vs_postcondition():
    """Test that verification distinguishes executor success from postcondition verification."""
    from graph.nodes.verify_step import verify_step_node
    from migration.graph_state import OperonixState
    from migration.domain_contracts import TaskRequest, TaskSource, ExecutionResult, TaskStatus
    
    # Test with executor success
    task = TaskRequest(user_input="Open Firefox", source=TaskSource.VOICE)
    state = OperonixState(task=task)
    state.execution = ExecutionResult(
        step_id="step_1",
        execution_status=TaskStatus.COMPLETED,
        result={"success": True}
    )
    
    result = verify_step_node(state)
    
    assert result["state"].verification is not None
    # Executor success should lead to postcondition verification
    assert result["state"].verification.status in ["VERIFIED", "UNCERTAIN"]


def test_verify_step_executor_failure():
    """Test that executor failure results in verification failure."""
    from graph.nodes.verify_step import verify_step_node
    from migration.graph_state import OperonixState
    from migration.domain_contracts import TaskRequest, TaskSource, ExecutionResult, TaskStatus
    
    task = TaskRequest(user_input="Open Firefox", source=TaskSource.VOICE)
    state = OperonixState(task=task)
    state.execution = ExecutionResult(
        step_id="step_1",
        execution_status=TaskStatus.FAILED,
        result={"error": "Command failed"}
    )
    
    result = verify_step_node(state)
    
    assert result["state"].verification is not None
    assert result["state"].verification.status == "FAILED"
    assert "executor reported failure" in result["state"].verification.reason


def test_verify_step_no_execution_result():
    """Test that missing execution result results in uncertain verification."""
    from graph.nodes.verify_step import verify_step_node
    from migration.graph_state import OperonixState
    from migration.domain_contracts import TaskRequest, TaskSource
    
    task = TaskRequest(user_input="Open Firefox", source=TaskSource.VOICE)
    state = OperonixState(task=task)
    
    result = verify_step_node(state)
    
    assert result["state"].verification is not None
    assert result["state"].verification.status == "UNCERTAIN"


def test_verify_step_execution_result_failure_flag():
    """Test that execution result with success=False results in verification failure."""
    from graph.nodes.verify_step import verify_step_node
    from migration.graph_state import OperonixState
    from migration.domain_contracts import TaskRequest, TaskSource, ExecutionResult, TaskStatus
    
    task = TaskRequest(user_input="Open Firefox", source=TaskSource.VOICE)
    state = OperonixState(task=task)
    state.execution = ExecutionResult(
        step_id="step_1",
        execution_status=TaskStatus.COMPLETED,
        result={"success": False, "error": "Tool not found"}
    )
    
    result = verify_step_node(state)
    
    assert result["state"].verification is not None
    assert result["state"].verification.status == "FAILED"
    assert "execution result indicates failure" in result["state"].verification.reason


def test_verify_step_history_tracking():
    """Test that verify_step node tracks history events."""
    from graph.nodes.verify_step import verify_step_node
    from migration.graph_state import OperonixState
    from migration.domain_contracts import TaskRequest, TaskSource, ExecutionResult, TaskStatus
    
    task = TaskRequest(user_input="Open Firefox", source=TaskSource.VOICE)
    state = OperonixState(task=task)
    state.execution = ExecutionResult(
        step_id="step_1",
        execution_status=TaskStatus.COMPLETED,
        result={"success": True}
    )
    
    result = verify_step_node(state)
    
    events = result["state"].history.get("events", [])
    assert len(events) >= 2  # verify_step_started, verify_step_completed
    
    event_types = [e["type"] for e in events]
    assert "verify_step_started" in event_types
    assert "verify_step_completed" in event_types


def test_verify_result_domain_object():
    """Test that VerificationResult is a valid domain object."""
    from migration.domain_contracts import VerificationResult, ContextSnapshot
    
    verification_result = VerificationResult(
        status="VERIFIED",
        observed_context=ContextSnapshot(),
        expected_state={"outcome": "file created"},
        actual_state={"outcome": "file created"},
        reason="Postconditions verified"
    )
    
    assert verification_result.status == "VERIFIED"
    assert verification_result.observed_context is not None
    assert verification_result.verification_timestamp is not None


# ─── RECOVERY TESTS ───────────────────────────────────────────────────────────

def test_recover_node_import():
    """Test that recover node can be imported."""
    from graph.nodes.recover import recover_node
    assert recover_node is not None


def test_recover_node_transient_failure():
    """Test that transient failure triggers retry strategy."""
    from graph.nodes.recover import recover_node
    from migration.graph_state import OperonixState
    from migration.domain_contracts import TaskRequest, TaskSource, VerificationResult, ContextSnapshot
    
    task = TaskRequest(user_input="Open Firefox", source=TaskSource.VOICE)
    state = OperonixState(task=task)
    state.verification = VerificationResult(
        status="FAILED",
        observed_context=ContextSnapshot(),
        reason="Transient timeout error"
    )
    
    result = recover_node(state)
    
    assert result["state"].recovery is not None
    assert result["state"].recovery.failure_category.value == "transient"
    assert result["state"].recovery.recovery_strategy.value == "retry"
    assert result["state"].recovery.target_stage == "execute_step"


def test_recover_node_context_mismatch():
    """Test that context mismatch triggers observe strategy."""
    from graph.nodes.recover import recover_node
    from migration.graph_state import OperonixState
    from migration.domain_contracts import TaskRequest, TaskSource, VerificationResult, ContextSnapshot
    
    task = TaskRequest(user_input="Open Firefox", source=TaskSource.VOICE)
    state = OperonixState(task=task)
    state.verification = VerificationResult(
        status="FAILED",
        observed_context=ContextSnapshot(),
        reason="Context mismatch: wrong directory"
    )
    
    result = recover_node(state)
    
    assert result["state"].recovery is not None
    assert result["state"].recovery.failure_category.value == "context_mismatch"
    assert result["state"].recovery.recovery_strategy.value == "observe"
    assert result["state"].recovery.target_stage == "observe"


def test_recover_node_routing_mismatch():
    """Test that routing mismatch triggers route strategy."""
    from graph.nodes.recover import recover_node
    from migration.graph_state import OperonixState
    from migration.domain_contracts import TaskRequest, TaskSource, VerificationResult, ContextSnapshot
    
    task = TaskRequest(user_input="Open Firefox", source=TaskSource.VOICE)
    state = OperonixState(task=task)
    state.verification = VerificationResult(
        status="FAILED",
        observed_context=ContextSnapshot(),
        reason="Routing mismatch: wrong method"
    )
    
    result = recover_node(state)
    
    assert result["state"].recovery is not None
    assert result["state"].recovery.failure_category.value == "routing_mismatch"
    assert result["state"].recovery.recovery_strategy.value == "route"
    assert result["state"].recovery.target_stage == "route"


def test_recover_node_tool_unavailable():
    """Test that tool unavailable triggers route strategy."""
    from graph.nodes.recover import recover_node
    from migration.graph_state import OperonixState
    from migration.domain_contracts import TaskRequest, TaskSource, VerificationResult, ContextSnapshot
    
    task = TaskRequest(user_input="Open Firefox", source=TaskSource.VOICE)
    state = OperonixState(task=task)
    state.verification = VerificationResult(
        status="FAILED",
        observed_context=ContextSnapshot(),
        reason="Tool unavailable: capability not found"
    )
    
    result = recover_node(state)
    
    assert result["state"].recovery is not None
    assert result["state"].recovery.failure_category.value == "tool_unavailable"
    assert result["state"].recovery.recovery_strategy.value == "route"
    assert result["state"].recovery.target_stage == "route"


def test_recover_node_planning_error():
    """Test that planning error triggers replan strategy."""
    from graph.nodes.recover import recover_node
    from migration.graph_state import OperonixState
    from migration.domain_contracts import TaskRequest, TaskSource, VerificationResult, ContextSnapshot
    
    task = TaskRequest(user_input="Open Firefox", source=TaskSource.VOICE)
    state = OperonixState(task=task)
    state.verification = VerificationResult(
        status="FAILED",
        observed_context=ContextSnapshot(),
        reason="Planning error: invalid step sequence"
    )
    
    result = recover_node(state)
    
    assert result["state"].recovery is not None
    assert result["state"].recovery.failure_category.value == "planning_error"
    assert result["state"].recovery.recovery_strategy.value == "replan"
    assert result["state"].recovery.target_stage == "create_plan"


def test_recover_node_permission_denied():
    """Test that permission denied triggers abort strategy."""
    from graph.nodes.recover import recover_node
    from migration.graph_state import OperonixState
    from migration.domain_contracts import TaskRequest, TaskSource, VerificationResult, ContextSnapshot
    
    task = TaskRequest(user_input="Open Firefox", source=TaskSource.VOICE)
    state = OperonixState(task=task)
    state.verification = VerificationResult(
        status="FAILED",
        observed_context=ContextSnapshot(),
        reason="Permission denied: insufficient privileges"
    )
    
    result = recover_node(state)
    
    assert result["state"].recovery is not None
    assert result["state"].recovery.failure_category.value == "permission_denied"
    assert result["state"].recovery.recovery_strategy.value == "abort"
    assert result["state"].recovery.target_stage == "finalize"


def test_recover_node_max_retries():
    """Test that max retries triggers abort strategy."""
    from graph.nodes.recover import recover_node
    from migration.graph_state import OperonixState
    from migration.domain_contracts import TaskRequest, TaskSource, VerificationResult, ContextSnapshot, RecoveryDecision, FailureCategory, RecoveryStrategy
    
    task = TaskRequest(user_input="Open Firefox", source=TaskSource.VOICE)
    state = OperonixState(task=task)
    state.verification = VerificationResult(
        status="FAILED",
        observed_context=ContextSnapshot(),
        reason="Transient timeout error"
    )
    state.recovery = RecoveryDecision(
        failure_category=FailureCategory.TRANSIENT,
        recovery_strategy=RecoveryStrategy.RETRY,
        retry_count=3  # Max retries reached
    )
    
    result = recover_node(state)
    
    assert result["state"].recovery is not None
    assert result["state"].recovery.recovery_strategy.value == "abort"
    assert result["state"].recovery.target_stage == "finalize"


def test_recover_node_history_tracking():
    """Test that recover node tracks history events."""
    from graph.nodes.recover import recover_node
    from migration.graph_state import OperonixState
    from migration.domain_contracts import TaskRequest, TaskSource, VerificationResult, ContextSnapshot
    
    task = TaskRequest(user_input="Open Firefox", source=TaskSource.VOICE)
    state = OperonixState(task=task)
    state.verification = VerificationResult(
        status="FAILED",
        observed_context=ContextSnapshot(),
        reason="Transient timeout error"
    )
    
    result = recover_node(state)
    
    events = result["state"].history.get("events", [])
    assert len(events) >= 2  # recover_started, recover_completed
    
    event_types = [e["type"] for e in events]
    assert "recover_started" in event_types
    assert "recover_completed" in event_types


def test_recovery_decision_domain_object():
    """Test that RecoveryDecision is a valid domain object."""
    from migration.domain_contracts import RecoveryDecision, FailureCategory, RecoveryStrategy
    
    recovery_decision = RecoveryDecision(
        failure_category=FailureCategory.TRANSIENT,
        recovery_strategy=RecoveryStrategy.RETRY,
        retry_count=1,
        target_stage="execute_step",
        reason="Transient failure, retrying"
    )
    
    assert recovery_decision.failure_category == FailureCategory.TRANSIENT
    assert recovery_decision.recovery_strategy == RecoveryStrategy.RETRY
    assert recovery_decision.retry_count == 1
    assert recovery_decision.target_stage == "execute_step"
    assert recovery_decision.decision_timestamp is not None


def test_failure_category_enum():
    """Test that FailureCategory enum has all required values."""
    from migration.domain_contracts import FailureCategory
    
    assert FailureCategory.TRANSIENT.value == "transient"
    assert FailureCategory.PERMANENT.value == "permanent"
    assert FailureCategory.ENVIRONMENTAL.value == "environmental"
    assert FailureCategory.CONTEXT_MISMATCH.value == "context_mismatch"
    assert FailureCategory.ROUTING_MISMATCH.value == "routing_mismatch"
    assert FailureCategory.TOOL_UNAVAILABLE.value == "tool_unavailable"
    assert FailureCategory.PERMISSION_DENIED.value == "permission_denied"
    assert FailureCategory.VALIDATION_REJECTED.value == "validation_rejected"
    assert FailureCategory.PLANNING_ERROR.value == "planning_error"
    assert FailureCategory.UNKNOWN.value == "unknown"


def test_recovery_strategy_enum():
    """Test that RecoveryStrategy enum has all required values."""
    from migration.domain_contracts import RecoveryStrategy
    
    assert RecoveryStrategy.RETRY.value == "retry"
    assert RecoveryStrategy.OBSERVE.value == "observe"
    assert RecoveryStrategy.ROUTE.value == "route"
    assert RecoveryStrategy.REPLAN.value == "replan"
    assert RecoveryStrategy.ABORT.value == "abort"


# ─── ERROR SEMANTICS TESTS ─────────────────────────────────────────────────────

def test_error_semantics_transient_failure():
    """Test transient failure error semantics."""
    from graph.nodes.recover import _classify_failure
    from migration.graph_state import OperonixState
    from migration.domain_contracts import TaskRequest, TaskSource, VerificationResult, ContextSnapshot
    
    task = TaskRequest(user_input="Open Firefox", source=TaskSource.VOICE)
    state = OperonixState(task=task)
    state.verification = VerificationResult(
        status="FAILED",
        observed_context=ContextSnapshot(),
        reason="Transient timeout error"
    )
    
    failure_category = _classify_failure(state)
    assert failure_category.value == "transient"


def test_error_semantics_context_mismatch():
    """Test context mismatch error semantics."""
    from graph.nodes.recover import _classify_failure
    from migration.graph_state import OperonixState
    from migration.domain_contracts import TaskRequest, TaskSource, VerificationResult, ContextSnapshot
    
    task = TaskRequest(user_input="Open Firefox", source=TaskSource.VOICE)
    state = OperonixState(task=task)
    state.verification = VerificationResult(
        status="FAILED",
        observed_context=ContextSnapshot(),
        reason="Context mismatch: wrong cwd"
    )
    
    failure_category = _classify_failure(state)
    assert failure_category.value == "context_mismatch"


def test_error_semantics_routing_mismatch():
    """Test routing mismatch error semantics."""
    from graph.nodes.recover import _classify_failure
    from migration.graph_state import OperonixState
    from migration.domain_contracts import TaskRequest, TaskSource, VerificationResult, ContextSnapshot
    
    task = TaskRequest(user_input="Open Firefox", source=TaskSource.VOICE)
    state = OperonixState(task=task)
    state.verification = VerificationResult(
        status="FAILED",
        observed_context=ContextSnapshot(),
        reason="Routing mismatch: wrong method selected"
    )
    
    failure_category = _classify_failure(state)
    assert failure_category.value == "routing_mismatch"


def test_error_semantics_tool_unavailable():
    """Test tool unavailable error semantics."""
    from graph.nodes.recover import _classify_failure
    from migration.graph_state import OperonixState
    from migration.domain_contracts import TaskRequest, TaskSource, VerificationResult, ContextSnapshot
    
    task = TaskRequest(user_input="Open Firefox", source=TaskSource.VOICE)
    state = OperonixState(task=task)
    state.verification = VerificationResult(
        status="FAILED",
        observed_context=ContextSnapshot(),
        reason="Tool unavailable: capability not found"
    )
    
    failure_category = _classify_failure(state)
    assert failure_category.value == "tool_unavailable"


def test_error_semantics_verification_failure():
    """Test verification failure error semantics."""
    from graph.nodes.recover import _classify_failure
    from migration.graph_state import OperonixState
    from migration.domain_contracts import TaskRequest, TaskSource, VerificationResult, ContextSnapshot
    
    task = TaskRequest(user_input="Open Firefox", source=TaskSource.VOICE)
    state = OperonixState(task=task)
    state.verification = VerificationResult(
        status="FAILED",
        observed_context=ContextSnapshot(),
        reason="Verification failed: postconditions not met"
    )
    
    failure_category = _classify_failure(state)
    # Verification failure without specific keywords falls to UNKNOWN
    assert failure_category.value == "unknown"


def test_error_semantics_planning_failure():
    """Test planning failure error semantics."""
    from graph.nodes.recover import _classify_failure
    from migration.graph_state import OperonixState
    from migration.domain_contracts import TaskRequest, TaskSource, VerificationResult, ContextSnapshot
    
    task = TaskRequest(user_input="Open Firefox", source=TaskSource.VOICE)
    state = OperonixState(task=task)
    state.verification = VerificationResult(
        status="FAILED",
        observed_context=ContextSnapshot(),
        reason="Planning error: invalid step sequence"
    )
    
    failure_category = _classify_failure(state)
    assert failure_category.value == "planning_error"


# ─── RECOVERY MAPPING TESTS ────────────────────────────────────────────────────

def test_recovery_mapping_transient_to_retry():
    """Test recovery mapping: TRANSIENT → retry."""
    from graph.nodes.recover import _determine_recovery_strategy
    from migration.domain_contracts import FailureCategory
    
    strategy = _determine_recovery_strategy(FailureCategory.TRANSIENT, None)
    assert strategy.value == "retry"


def test_recovery_mapping_context_mismatch_to_observe():
    """Test recovery mapping: CONTEXT_MISMATCH → observe."""
    from graph.nodes.recover import _determine_recovery_strategy
    from migration.domain_contracts import FailureCategory
    
    strategy = _determine_recovery_strategy(FailureCategory.CONTEXT_MISMATCH, None)
    assert strategy.value == "observe"


def test_recovery_mapping_routing_mismatch_to_route():
    """Test recovery mapping: ROUTING_MISMATCH → route."""
    from graph.nodes.recover import _determine_recovery_strategy
    from migration.domain_contracts import FailureCategory
    
    strategy = _determine_recovery_strategy(FailureCategory.ROUTING_MISMATCH, None)
    assert strategy.value == "route"


def test_recovery_mapping_tool_unavailable_to_route():
    """Test recovery mapping: TOOL_UNAVAILABLE → route."""
    from graph.nodes.recover import _determine_recovery_strategy
    from migration.domain_contracts import FailureCategory
    
    strategy = _determine_recovery_strategy(FailureCategory.TOOL_UNAVAILABLE, None)
    assert strategy.value == "route"


def test_recovery_mapping_planning_error_to_replan():
    """Test recovery mapping: PLANNING_ERROR → replan."""
    from graph.nodes.recover import _determine_recovery_strategy
    from migration.domain_contracts import FailureCategory
    
    strategy = _determine_recovery_strategy(FailureCategory.PLANNING_ERROR, None)
    assert strategy.value == "replan"


def test_recovery_mapping_permission_denied_to_abort():
    """Test recovery mapping: PERMISSION_DENIED → abort."""
    from graph.nodes.recover import _determine_recovery_strategy
    from migration.domain_contracts import FailureCategory
    
    strategy = _determine_recovery_strategy(FailureCategory.PERMISSION_DENIED, None)
    assert strategy.value == "abort"


def test_recovery_mapping_unknown_to_abort():
    """Test recovery mapping: UNKNOWN → abort."""
    from graph.nodes.recover import _determine_recovery_strategy
    from migration.domain_contracts import FailureCategory
    
    strategy = _determine_recovery_strategy(FailureCategory.UNKNOWN, None)
    assert strategy.value == "abort"
