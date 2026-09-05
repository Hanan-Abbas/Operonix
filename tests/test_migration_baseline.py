"""
Migration Baseline Tests — Operonix Phase 0
───────────────────────────────────────────

Baseline regression tests to establish current behavior before migration.
These tests will be used to detect regressions during migration.

Per migration plan §16.7, these tests provide the behavioral baseline
for comparison with the new graph-based implementation.
"""
from __future__ import annotations

import pytest
from pathlib import Path
from typing import Any


# ─── BASELINE REGISTRY TESTS ─────────────────────────────────────────────────

def test_baseline_registry_can_be_created():
    """Test that baseline registry can be instantiated."""
    from migration.baseline import get_baseline_registry
    
    registry = get_baseline_registry()
    assert registry is not None
    assert registry.project_root.exists()


def test_baseline_can_be_established():
    """Test that migration baseline can be established."""
    from migration.baseline import establish_migration_baseline
    
    baseline_info = establish_migration_baseline()
    
    assert "baseline_commit" in baseline_info
    assert "baseline_branch" in baseline_info
    assert "established_at" in baseline_info
    assert "phase" in baseline_info
    assert baseline_info["phase"] == "Phase 0: Baseline, Contracts & Safety"


def test_baseline_is_persisted():
    """Test that baseline data is persisted to file."""
    from migration.baseline import get_baseline_registry
    
    registry = get_baseline_registry()
    registry.establish_baseline()
    
    assert registry.baseline_file.exists()
    
    # Reload and verify
    new_registry = get_baseline_registry()
    assert new_registry.is_baseline_established()


def test_baseline_has_critical_workflows():
    """Test that baseline includes critical workflow definitions."""
    from migration.baseline import get_baseline_registry
    
    registry = get_baseline_registry()
    workflows = registry._get_critical_workflows()
    
    assert len(workflows) > 0
    
    # Verify required workflows are present
    workflow_names = [w["name"] for w in workflows]
    required_workflows = [
        "simple_file_operation",
        "application_opening",
        "shell_operation",
        "ui_operation",
        "multi_step_workflow",
        "failure_retry",
        "safety_rejection",
        "confirmation_required",
    ]
    
    for required in required_workflows:
        assert required in workflow_names, f"Required workflow {required} not found"


# ─── FEATURE FLAGS TESTS ───────────────────────────────────────────────────────

def test_feature_flags_default_to_false():
    """Test that all migration feature flags default to False for safety."""
    from migration.feature_flags import flags
    
    # All migration flags should default to False
    assert flags.USE_LANGGRAPH is False
    assert flags.USE_LANGCHAIN_MODELS is False
    assert flags.USE_GRAPH_ROUTING is False
    assert flags.USE_GRAPH_EXECUTION is False
    assert flags.USE_VERIFICATION is False
    assert flags.USE_RECOVERY is False
    assert flags.USE_CHECKPOINTING is False
    assert flags.USE_CANDIDATE_ROUTING is False
    assert flags.USE_TOOL_ADAPTERS is False
    assert flags.USE_RAG_MEMORY is False
    assert flags.USE_LEARNING_ROUTING is False


def test_feature_flags_safety_strict_mode_defaults_true():
    """Test that safety strict mode defaults to True."""
    from migration.feature_flags import flags
    
    assert flags.SAFETY_STRICT_MODE is True
    assert flags.SAFETY_ALLOW_BYPASS is False


def test_feature_flags_can_be_overridden():
    """Test that feature flags can be overridden via environment."""
    import os
    from migration.feature_flags import FeatureFlags
    
    # Temporarily set environment variable
    os.environ["USE_LANGGRAPH"] = "true"
    
    # Create new instance to pick up env var
    test_flags = FeatureFlags()
    assert test_flags.USE_LANGGRAPH is True
    
    # Clean up
    del os.environ["USE_LANGGRAPH"]


def test_feature_flags_migration_phase_detection():
    """Test that migration phase is correctly detected based on flags."""
    from migration.feature_flags import flags
    
    # Default should be Phase 0
    assert "Phase 0" in flags.get_migration_phase()
    
    # With no migration active
    assert flags.is_migration_active() is False


def test_feature_flags_get_all_flags():
    """Test that all flags can be retrieved as dictionary."""
    from migration.feature_flags import flags
    
    all_flags = flags.get_all_flags()
    
    assert isinstance(all_flags, dict)
    assert len(all_flags) > 0
    
    # Verify some expected flags
    assert "USE_LANGGRAPH" in all_flags
    assert "USE_LANGCHAIN_MODELS" in all_flags
    assert "SAFETY_STRICT_MODE" in all_flags


# ─── DOMAIN CONTRACTS TESTS ───────────────────────────────────────────────────

def test_task_request_contract():
    """Test TaskRequest domain contract."""
    from migration.domain_contracts import TaskRequest, TaskSource
    
    request = TaskRequest(
        user_input="Open Firefox",
        source=TaskSource.VOICE
    )
    
    assert request.task_id is not None
    assert request.user_input == "Open Firefox"
    assert request.source == TaskSource.VOICE
    assert request.created_at is not None


def test_intent_result_contract():
    """Test IntentResult domain contract."""
    from migration.domain_contracts import IntentResult
    
    intent = IntentResult(
        name="open_application",
        confidence=0.95,
        parameters={"app": "firefox"}
    )
    
    assert intent.name == "open_application"
    assert intent.confidence == 0.95
    assert intent.parameters["app"] == "firefox"


def test_context_snapshot_contract():
    """Test ContextSnapshot domain contract."""
    from migration.domain_contracts import ContextSnapshot
    
    context = ContextSnapshot(
        active_window="Firefox",
        app="firefox",
        cwd="/home/user"
    )
    
    assert context.active_window == "Firefox"
    assert context.app == "firefox"
    assert context.cwd == "/home/user"
    assert context.captured_at is not None


def test_plan_and_plan_step_contracts():
    """Test Plan and PlanStep domain contracts."""
    from migration.domain_contracts import Plan, PlanStep
    
    step = PlanStep(
        step_id="step_1",
        action="open_application",
        arguments={"app": "firefox"},
        objective="Open Firefox browser"
    )
    
    plan = Plan(steps=[step])
    
    assert len(plan.steps) == 1
    assert plan.current_step == step
    assert plan.is_complete is False


def test_routing_candidate_contract():
    """Test RoutingCandidate domain contract."""
    from migration.domain_contracts import RoutingCandidate
    
    candidate = RoutingCandidate(
        method_type="PLUGIN",
        tool_id="firefox_plugin",
        capability_fit=0.9,
        context_fit=0.8,
        overall_score=0.85
    )
    
    assert candidate.method_type == "PLUGIN"
    assert candidate.tool_id == "firefox_plugin"
    assert candidate.overall_score == 0.85


def test_method_decision_contract():
    """Test MethodDecision domain contract."""
    from migration.domain_contracts import MethodDecision, RoutingCandidate
    
    candidate = RoutingCandidate(
        method_type="SHELL",
        overall_score=0.9
    )
    
    decision = MethodDecision(
        selected_candidate=candidate,
        confidence=0.9
    )
    
    assert decision.selected_candidate == candidate
    assert decision.confidence == 0.9
    assert decision.decision_timestamp is not None


def test_safety_decision_contract():
    """Test SafetyDecision domain contract."""
    from migration.domain_contracts import SafetyDecision, RiskLevel
    
    safety = SafetyDecision(
        risk_level=RiskLevel.LOW,
        validation_status="APPROVED",
        permission_status="GRANTED"
    )
    
    assert safety.risk_level == RiskLevel.LOW
    assert safety.validation_status == "APPROVED"
    assert safety.confirmation_required is False


def test_execution_result_contract():
    """Test ExecutionResult domain contract."""
    from migration.domain_contracts import ExecutionResult, TaskStatus
    
    result = ExecutionResult(
        execution_id="exec_123",
        step_id="step_1",
        success=True,
        method_used="SHELL",
        execution_status=TaskStatus.COMPLETED
    )
    
    assert result.execution_id == "exec_123"
    assert result.success is True
    assert result.execution_status == TaskStatus.COMPLETED


def test_verification_result_contract():
    """Test VerificationResult domain contract."""
    from migration.domain_contracts import VerificationResult, ContextSnapshot
    
    context = ContextSnapshot(active_window="Firefox")
    verification = VerificationResult(
        status="VERIFIED",
        observed_context=context
    )
    
    assert verification.status == "VERIFIED"
    assert verification.observed_context == context


def test_recovery_decision_contract():
    """Test RecoveryDecision domain contract."""
    from migration.domain_contracts import RecoveryDecision, FailureCategory, RecoveryStrategy
    
    recovery = RecoveryDecision(
        failure_category=FailureCategory.TRANSIENT,
        recovery_strategy=RecoveryStrategy.RETRY
    )
    
    assert recovery.failure_category == FailureCategory.TRANSIENT
    assert recovery.recovery_strategy == RecoveryStrategy.RETRY


def test_reflection_result_contract():
    """Test ReflectionResult domain contract."""
    from migration.domain_contracts import ReflectionResult, OutcomeGrade
    
    reflection = ReflectionResult(
        outcome=OutcomeGrade.GOOD
    )
    
    assert reflection.outcome == OutcomeGrade.GOOD


def test_final_result_contract():
    """Test FinalResult domain contract."""
    from migration.domain_contracts import FinalResult
    
    final = FinalResult(
        success=True,
        response="Firefox opened successfully",
        task_id="task_123"
    )
    
    assert final.success is True
    assert final.response == "Firefox opened successfully"
    assert final.task_id == "task_123"


# ─── GRAPH STATE TESTS ────────────────────────────────────────────────────────

def test_operonix_state_creation():
    """Test OperonixState can be created with task."""
    from migration.graph_state import OperonixState
    from migration.domain_contracts import TaskRequest, TaskSource
    
    task = TaskRequest(
        user_input="Open Firefox",
        source=TaskSource.VOICE
    )
    
    state = OperonixState(task=task)
    
    assert state.task == task
    assert state.intent is None
    assert state.plan is None
    assert state.state_version == "1.0.0"
    assert state.created_at is not None


def test_operonix_state_status_tracking():
    """Test OperonixState status determination."""
    from migration.graph_state import OperonixState
    from migration.domain_contracts import TaskRequest, TaskSource, FinalResult
    
    task = TaskRequest(user_input="test", source=TaskSource.API)
    state = OperonixState(task=task)
    
    # Initial status should be PENDING
    assert state.get_status().value == "pending"
    
    # With final result
    final = FinalResult(success=True, response="Done", task_id=task.task_id)
    state.final = final
    assert state.get_status().value == "completed"


def test_operonix_state_history_logging():
    """Test OperonixState history event logging."""
    from migration.graph_state import OperonixState
    from migration.domain_contracts import TaskRequest, TaskSource
    
    task = TaskRequest(user_input="test", source=TaskSource.API)
    state = OperonixState(task=task)
    
    state.add_history_event("test_event", {"key": "value"})
    
    assert "events" in state.history
    assert len(state.history["events"]) == 1
    assert state.history["events"][0]["type"] == "test_event"


def test_checkpoint_state_creation():
    """Test CheckpointState can be created."""
    from migration.graph_state import CheckpointState, OperonixState
    from migration.domain_contracts import TaskRequest, TaskSource
    
    task = TaskRequest(user_input="test", source=TaskSource.API)
    workflow_state = OperonixState(task=task)
    
    checkpoint = CheckpointState(
        task_id=task.task_id,
        workflow_state=workflow_state
    )
    
    assert checkpoint.task_id == task.task_id
    assert checkpoint.workflow_state == workflow_state
    assert checkpoint.checkpoint_id is not None


# ─── CONTRACT VALIDATION TESTS ─────────────────────────────────────────────────

def test_intent_result_confidence_validation():
    """Test that IntentResult validates confidence range."""
    from migration.domain_contracts import IntentResult
    from pydantic import ValidationError
    
    # Valid confidence
    intent = IntentResult(name="test", confidence=0.5)
    assert intent.confidence == 0.5
    
    # Invalid confidence (too high)
    with pytest.raises(ValidationError):
        IntentResult(name="test", confidence=1.5)
    
    # Invalid confidence (negative)
    with pytest.raises(ValidationError):
        IntentResult(name="test", confidence=-0.1)


def test_routing_candidate_score_validation():
    """Test that RoutingCandidate validates score ranges."""
    from migration.domain_contracts import RoutingCandidate
    from pydantic import ValidationError
    
    # Valid scores
    candidate = RoutingCandidate(
        method_type="TEST",
        capability_fit=0.5,
        overall_score=0.8
    )
    assert candidate.capability_fit == 0.5
    
    # Invalid score
    with pytest.raises(ValidationError):
        RoutingCandidate(method_type="TEST", capability_fit=1.5)


def test_plan_step_idempotency_validation():
    """Test that PlanStep validates idempotency enum."""
    from migration.domain_contracts import PlanStep
    from pydantic import ValidationError
    
    # Valid idempotency
    step = PlanStep(step_id="test", action="test", idempotency="SAFE")
    assert step.idempotency == "SAFE"
    
    # Invalid idempotency
    with pytest.raises(ValidationError):
        PlanStep(step_id="test", action="test", idempotency="INVALID")


# ─── INTEGRATION TESTS ────────────────────────────────────────────────────────

def test_migration_package_imports():
    """Test that migration package can be imported."""
    import migration
    from migration import feature_flags, domain_contracts, graph_state, baseline
    
    assert migration is not None
    assert feature_flags is not None
    assert domain_contracts is not None
    assert graph_state is not None
    assert baseline is not None


def test_domain_contracts_serialization():
    """Test that domain contracts can be serialized to JSON."""
    from migration.domain_contracts import TaskRequest, TaskSource
    import json
    
    request = TaskRequest(user_input="test", source=TaskSource.VOICE)
    
    # Should serialize without error
    json_str = request.json()
    assert json_str is not None
    
    # Should deserialize back
    deserialized = TaskRequest.parse_raw(json_str)
    assert deserialized.user_input == request.user_input
    assert deserialized.source == request.source


if __name__ == "__main__":
    pytest.main([__file__, "-v"])
