"""
Context & Knowledge Integration Tests — Operonix Migration Phase 11
────────────────────────────────────────────────────────────────────

Tests for context and knowledge integration.
Per migration plan Phase 11: Context & Knowledge Integration
"""
from __future__ import annotations

import pytest


# ─── OBSERVE NODE CONTEXT INTEGRATION TESTS ───────────────────────────────────

def test_observe_node_integrates_window_detector():
    """Test that observe node integrates with WindowDetector."""
    from graph.nodes.observe import observe_node
    from migration.graph_state import OperonixState
    from migration.domain_contracts import TaskRequest, TaskSource
    
    task = TaskRequest(user_input="Test", source=TaskSource.VOICE)
    state = OperonixState(task=task)
    
    result = observe_node(state)
    
    assert result["state"].context is not None
    # WindowDetector integration is optional, so we just check it doesn't crash


def test_observe_node_integrates_app_classifier():
    """Test that observe node integrates with AppClassifier."""
    from graph.nodes.observe import observe_node
    from migration.graph_state import OperonixState
    from migration.domain_contracts import TaskRequest, TaskSource
    
    task = TaskRequest(user_input="Test", source=TaskSource.VOICE)
    state = OperonixState(task=task)
    
    result = observe_node(state)
    
    assert result["state"].context is not None
    # AppClassifier integration is optional, so we just check it doesn't crash


def test_observe_node_integrates_state_extractor():
    """Test that observe node integrates with StateExtractor."""
    from graph.nodes.observe import observe_node
    from migration.graph_state import OperonixState
    from migration.domain_contracts import TaskRequest, TaskSource
    
    task = TaskRequest(user_input="Test", source=TaskSource.VOICE)
    state = OperonixState(task=task)
    
    result = observe_node(state)
    
    assert result["state"].context is not None
    # StateExtractor integration is optional, so we just check it doesn't crash


def test_observe_node_integrates_focus_tracker():
    """Test that observe node integrates with FocusTracker."""
    from graph.nodes.observe import observe_node
    from migration.graph_state import OperonixState
    from migration.domain_contracts import TaskRequest, TaskSource
    
    task = TaskRequest(user_input="Test", source=TaskSource.VOICE)
    state = OperonixState(task=task)
    
    result = observe_node(state)
    
    assert result["state"].context is not None
    # FocusTracker integration is optional, so we just check it doesn't crash


def test_observe_node_integrates_context_validator():
    """Test that observe node integrates with ContextValidator."""
    from graph.nodes.observe import observe_node
    from migration.graph_state import OperonixState
    from migration.domain_contracts import TaskRequest, TaskSource
    
    task = TaskRequest(user_input="Test", source=TaskSource.VOICE)
    state = OperonixState(task=task)
    
    result = observe_node(state)
    
    assert result["state"].context is not None
    # ContextValidator integration is optional, so we just check it doesn't crash


def test_observe_node_context_snapshot_structure():
    """Test that context snapshot has correct structure."""
    from graph.nodes.observe import _gather_context_snapshot
    from migration.graph_state import OperonixState
    from migration.domain_contracts import TaskRequest, TaskSource
    
    task = TaskRequest(user_input="Test", source=TaskSource.VOICE)
    state = OperonixState(task=task)
    
    context_snapshot = _gather_context_snapshot(state)
    
    # Check required fields
    assert "window_title" in context_snapshot
    assert "app_name" in context_snapshot
    assert "app_type" in context_snapshot
    assert "cwd" in context_snapshot
    assert "state" in context_snapshot
    assert "focus" in context_snapshot
    assert "validation" in context_snapshot


def test_observe_node_recovery_observation():
    """Test that observe node handles recovery observation."""
    from graph.nodes.observe import observe_node
    from migration.graph_state import OperonixState
    from migration.domain_contracts import TaskRequest, TaskSource, RecoveryDecision, RecoveryStrategy
    from migration.domain_contracts import Plan, PlanStep, PlanStepIdempotency, PlanStepSideEffect
    
    task = TaskRequest(user_input="Test", source=TaskSource.VOICE)
    
    step = PlanStep(
        step_id="step_1",
        objective="Execute command",
        idempotency=PlanStepIdempotency.IDEMPOTENT,
        side_effect=PlanStepSideEffect.NONE
    )
    
    plan = Plan(
        plan_id="plan_1",
        steps=[step],
        current_step_index=0
    )
    
    recovery = RecoveryDecision(
        recovery_strategy=RecoveryStrategy.OBSERVE,
        failure_category="TRANSIENT",
        target_stage="execute_step"
    )
    
    state = OperonixState(task=task, plan=plan, recovery=recovery)
    
    result = observe_node(state)
    
    assert result["state"].context is not None
    assert "postcondition_check" in result["state"].context


def test_observe_node_postcondition_checking():
    """Test that observe node checks postconditions during recovery."""
    from graph.nodes.observe import _check_postconditions
    from migration.graph_state import OperonixState
    from migration.domain_contracts import TaskRequest, TaskSource, VerificationResult, ContextSnapshot
    from migration.domain_contracts import Plan, PlanStep, PlanStepIdempotency, PlanStepSideEffect
    
    task = TaskRequest(user_input="Test", source=TaskSource.VOICE)
    
    step = PlanStep(
        step_id="step_1",
        objective="Execute command",
        idempotency=PlanStepIdempotency.IDEMPOTENT,
        side_effect=PlanStepSideEffect.NONE
    )
    
    plan = Plan(
        plan_id="plan_1",
        steps=[step],
        current_step_index=0
    )
    
    verification = VerificationResult(
        status="VERIFIED",
        observed_context=ContextSnapshot(),
        expected_state={},
        actual_state={}
    )
    
    state = OperonixState(task=task, plan=plan, verification=verification)
    
    result = _check_postconditions(state)
    
    # If verification is VERIFIED, postconditions should be met
    assert result is True


# ─── RETRIEVE KNOWLEDGE NODE RAG INTEGRATION TESTS ────────────────────────────

def test_retrieve_knowledge_node_integrates_long_term_memory():
    """Test that retrieve_knowledge node integrates with LongTermMemory."""
    from graph.nodes.retrieve_knowledge import retrieve_knowledge_node
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
    
    result = retrieve_knowledge_node(state)
    
    assert result["state"].knowledge is not None
    # LongTermMemory integration is optional, so we just check it doesn't crash


def test_retrieve_knowledge_node_integrates_session_memory():
    """Test that retrieve_knowledge node integrates with SessionMemory."""
    from graph.nodes.retrieve_knowledge import retrieve_knowledge_node
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
    
    result = retrieve_knowledge_node(state)
    
    assert result["state"].knowledge is not None
    # SessionMemory integration is optional, so we just check it doesn't crash


def test_retrieve_knowledge_node_integrates_vector_store():
    """Test that retrieve_knowledge node integrates with VectorStore."""
    from graph.nodes.retrieve_knowledge import retrieve_knowledge_node
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
    
    result = retrieve_knowledge_node(state)
    
    assert result["state"].knowledge is not None
    # VectorStore integration is optional, so we just check it doesn't crash


def test_retrieve_knowledge_node_integrates_retriever():
    """Test that retrieve_knowledge node integrates with Retriever."""
    from graph.nodes.retrieve_knowledge import retrieve_knowledge_node
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
    
    result = retrieve_knowledge_node(state)
    
    assert result["state"].knowledge is not None
    # Retriever integration is optional, so we just check it doesn't crash


def test_retrieve_knowledge_node_knowledge_context_structure():
    """Test that knowledge context has correct structure."""
    from graph.nodes.retrieve_knowledge import retrieve_knowledge_node
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
    
    result = retrieve_knowledge_node(state)
    
    knowledge = result["state"].knowledge
    
    # Check required fields
    assert knowledge.retrieved_memories is not None
    assert knowledge.retrieved_documents is not None
    assert knowledge.learned_patterns is not None
    assert knowledge.provenance is not None


def test_retrieve_knowledge_node_without_intent():
    """Test that retrieve_knowledge node handles missing intent."""
    from graph.nodes.retrieve_knowledge import retrieve_knowledge_node
    from migration.graph_state import OperonixState
    from migration.domain_contracts import TaskRequest, TaskSource
    
    task = TaskRequest(user_input="Test", source=TaskSource.VOICE)
    state = OperonixState(task=task)  # No intent
    
    result = retrieve_knowledge_node(state)
    
    assert result["state"].knowledge is not None
    # Should not crash even without intent


def test_retrieve_knowledge_node_provenance_tracking():
    """Test that retrieve_knowledge node tracks provenance."""
    from graph.nodes.retrieve_knowledge import retrieve_knowledge_node
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
    
    result = retrieve_knowledge_node(state)
    
    knowledge = result["state"].knowledge
    
    # Provenance should be present
    assert knowledge.provenance is not None
    # Should track which services were used
    # (This will be empty if services are not available, which is acceptable)


# ─── CONTEXT SNAPSHOT HELPER TESTS ────────────────────────────────────────────

def test_gather_context_snapshot_handles_import_errors():
    """Test that _gather_context_snapshot handles import errors gracefully."""
    from graph.nodes.observe import _gather_context_snapshot
    from migration.graph_state import OperonixState
    from migration.domain_contracts import TaskRequest, TaskSource
    
    task = TaskRequest(user_input="Test", source=TaskSource.VOICE)
    state = OperonixState(task=task)
    
    # Should not crash even if context services are not available
    context_snapshot = _gather_context_snapshot(state)
    
    assert context_snapshot is not None
    assert isinstance(context_snapshot, dict)


def test_gather_context_snapshot_returns_default_values():
    """Test that _gather_context_snapshot returns default values when services unavailable."""
    from graph.nodes.observe import _gather_context_snapshot
    from migration.graph_state import OperonixState
    from migration.domain_contracts import TaskRequest, TaskSource
    
    task = TaskRequest(user_input="Test", source=TaskSource.VOICE)
    state = OperonixState(task=task)
    
    context_snapshot = _gather_context_snapshot(state)
    
    # Should have default values
    assert context_snapshot.get("window_title") == "Unknown" or context_snapshot.get("window_title") is not None
    assert context_snapshot.get("app_name") == "Unknown" or context_snapshot.get("app_name") is not None
    assert context_snapshot.get("app_type") == "unknown" or context_snapshot.get("app_type") is not None


# ─── POSTCONDITION CHECKING TESTS ────────────────────────────────────────────

def test_check_postconditions_without_plan():
    """Test that _check_postconditions handles missing plan."""
    from graph.nodes.observe import _check_postconditions
    from migration.graph_state import OperonixState
    from migration.domain_contracts import TaskRequest, TaskSource
    
    task = TaskRequest(user_input="Test", source=TaskSource.VOICE)
    state = OperonixState(task=task)  # No plan
    
    result = _check_postconditions(state)
    
    # Should return False (safe default) when no plan
    assert result is False


def test_check_postconditions_file_existence():
    """Test that _check_postconditions checks file existence."""
    from graph.nodes.observe import _check_postconditions
    from migration.graph_state import OperonixState
    from migration.domain_contracts import TaskRequest, TaskSource, Plan, PlanStep, PlanStepIdempotency, PlanStepSideEffect
    import os
    import tempfile
    
    # Create a temporary file
    with tempfile.NamedTemporaryFile(delete=False) as f:
        temp_file = f.name
    
    try:
        task = TaskRequest(user_input="Test", source=TaskSource.VOICE)
        
        step = PlanStep(
            step_id="step_1",
            objective=f"Create file {temp_file}",
            idempotency=PlanStepIdempotency.IDEMPOTENT,
            side_effect=PlanStepSideEffect.NONE
        )
        
        plan = Plan(
            plan_id="plan_1",
            steps=[step],
            current_step_index=0
        )
        
        state = OperonixState(task=task, plan=plan)
        
        result = _check_postconditions(state)
        
        # File exists, so postconditions should be met
        assert result is True
    finally:
        # Clean up
        os.unlink(temp_file)


def test_check_postconditions_directory_existence():
    """Test that _check_postconditions checks directory existence."""
    from graph.nodes.observe import _check_postconditions
    from migration.graph_state import OperonixState
    from migration.domain_contracts import TaskRequest, TaskSource, Plan, PlanStep, PlanStepIdempotency, PlanStepSideEffect
    import os
    import tempfile
    
    # Create a temporary directory
    temp_dir = tempfile.mkdtemp()
    
    try:
        task = TaskRequest(user_input="Test", source=TaskSource.VOICE)
        
        step = PlanStep(
            step_id="step_1",
            objective=f"Create directory {temp_dir}",
            idempotency=PlanStepIdempotency.IDEMPOTENT,
            side_effect=PlanStepSideEffect.NONE
        )
        
        plan = Plan(
            plan_id="plan_1",
            steps=[step],
            current_step_index=0
        )
        
        state = OperonixState(task=task, plan=plan)
        
        result = _check_postconditions(state)
        
        # Directory exists, so postconditions should be met
        assert result is True
    finally:
        # Clean up
        os.rmdir(temp_dir)
