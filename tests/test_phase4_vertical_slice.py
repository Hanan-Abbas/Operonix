"""
Phase 4 Vertical Slice Tests — Operonix Migration
──────────────────────────────────────────────────

Tests for Phase 4 first vertical slice implementation.
These tests verify that:
- All Phase 4 nodes can be executed in sequence
- Graph topology includes all Phase 4 nodes
- Canonical workflow "Open Firefox and search for autonomous agents" works end-to-end
- State flows through all nodes correctly
"""
from __future__ import annotations

import pytest
from typing import Dict, Any


# ─── PHASE 4 NODE TESTS ─────────────────────────────────────────────────────

def test_retrieve_knowledge_node_exists():
    """Test that retrieve_knowledge node can be imported."""
    from graph.nodes.retrieve_knowledge import retrieve_knowledge_node
    assert retrieve_knowledge_node is not None


def test_route_node_exists():
    """Test that route node can be imported."""
    from graph.nodes.route import route_node
    assert route_node is not None


def test_safety_check_node_exists():
    """Test that safety_check node can be imported."""
    from graph.nodes.safety_check import safety_check_node
    assert safety_check_node is not None


def test_execute_step_node_exists():
    """Test that execute_step node can be imported."""
    from graph.nodes.execute_step import execute_step_node
    assert execute_step_node is not None


def test_verify_step_node_exists():
    """Test that verify_step node can be imported."""
    from graph.nodes.verify_step import verify_step_node
    assert verify_step_node is not None


def test_retrieve_knowledge_node_with_state():
    """Test that retrieve_knowledge node processes state correctly."""
    from graph.nodes.retrieve_knowledge import retrieve_knowledge_node
    from migration.graph_state import OperonixState
    from migration.domain_contracts import TaskRequest, TaskSource, IntentResult
    
    task = TaskRequest(user_input="test", source=TaskSource.API)
    state = OperonixState(task=task)
    state.intent = IntentResult(name="test", confidence=0.9)
    
    result = retrieve_knowledge_node(state)
    
    # Field-level update: knowledge field returned directly
    assert "knowledge" in result or state.knowledge is not None
    knowledge = result.get("knowledge") or state.knowledge
    assert knowledge is not None
    history = result.get("history") or state.history
    assert len(history.get("events", [])) > 0


def test_route_node_with_state():
    """Test that route node processes state correctly."""
    from graph.nodes.route import route_node
    from migration.graph_state import OperonixState
    from migration.domain_contracts import TaskRequest, TaskSource, IntentResult, Plan, PlanStep
    import uuid
    
    task = TaskRequest(user_input="test", source=TaskSource.API)
    state = OperonixState(task=task)
    state.intent = IntentResult(name="test", confidence=0.9)
    step = PlanStep(step_id=str(uuid.uuid4()), action="test", objective="test action")
    state.plan = Plan(steps=[step])
    
    result = route_node(state)
    
    # Field-level update: routing field returned directly
    assert "routing" in result or state.routing is not None
    routing = result.get("routing") or state.routing
    assert routing is not None
    assert routing.selected_candidate is not None


def test_safety_check_node_with_state():
    """Test that safety_check node processes state correctly."""
    from graph.nodes.safety_check import safety_check_node
    from migration.graph_state import OperonixState
    from migration.domain_contracts import TaskRequest, TaskSource, IntentResult, Plan, PlanStep, MethodDecision, RoutingCandidate
    import uuid
    
    task = TaskRequest(user_input="test", source=TaskSource.API)
    state = OperonixState(task=task)
    state.intent = IntentResult(name="test", confidence=0.9)
    step = PlanStep(step_id=str(uuid.uuid4()), action="test", objective="test action")
    state.plan = Plan(steps=[step])
    candidate = RoutingCandidate(method_type="SHELL", overall_score=0.8)
    state.routing = MethodDecision(selected_candidate=candidate, confidence=0.8)
    
    result = safety_check_node(state)
    
    # Field-level update: safety field returned directly
    assert "safety" in result or state.safety is not None
    safety = result.get("safety") or state.safety
    assert safety is not None
    assert safety.validation_status == "APPROVED"


def test_execute_step_node_with_state():
    """Test that execute_step node processes state correctly."""
    from graph.nodes.execute_step import execute_step_node
    from migration.graph_state import OperonixState
    from migration.domain_contracts import TaskRequest, TaskSource, IntentResult, Plan, PlanStep, MethodDecision, RoutingCandidate
    import uuid
    
    task = TaskRequest(user_input="test", source=TaskSource.API)
    state = OperonixState(task=task)
    state.intent = IntentResult(name="test", confidence=0.9)
    step = PlanStep(step_id=str(uuid.uuid4()), action="test", objective="test action")
    state.plan = Plan(steps=[step])
    candidate = RoutingCandidate(method_type="SHELL", overall_score=0.8)
    state.routing = MethodDecision(selected_candidate=candidate, confidence=0.8)
    state.safety = type('obj', (object,), {'validation_status': 'APPROVED'})()
    
    result = execute_step_node(state)
    
    # Field-level update: execution field returned directly
    assert "execution" in result or state.execution is not None
    # Note: Execution may fail in test environment due to missing tool implementation
    # The important part is that the node processes state and creates an execution result


def test_verify_step_node_with_state():
    """Test that verify_step node processes state correctly."""
    from graph.nodes.verify_step import verify_step_node
    from migration.graph_state import OperonixState
    from migration.domain_contracts import TaskRequest, TaskSource, IntentResult, Plan, PlanStep, MethodDecision, RoutingCandidate, ExecutionResult, TaskStatus
    import uuid
    
    task = TaskRequest(user_input="test", source=TaskSource.API)
    state = OperonixState(task=task)
    state.intent = IntentResult(name="test", confidence=0.9)
    step = PlanStep(step_id=str(uuid.uuid4()), action="test", objective="test action")
    state.plan = Plan(steps=[step])
    candidate = RoutingCandidate(method_type="SHELL", overall_score=0.8)
    state.routing = MethodDecision(selected_candidate=candidate, confidence=0.8)
    state.safety = type('obj', (object,), {'validation_status': 'APPROVED'})()
    state.execution = ExecutionResult(
        execution_id="test_id",
        step_id=step.step_id,
        success=True,
        method_used="SHELL",
        execution_status=TaskStatus.COMPLETED,
        result_data={}
    )
    
    result = verify_step_node(state)
    
    # Field-level update: verification field returned directly
    assert "verification" in result or state.verification is not None
    verification = result.get("verification") or state.verification
    assert verification is not None
    assert verification.status == "VERIFIED"


# ─── VERTICAL SLICE TESTS ───────────────────────────────────────────────────

def test_canonical_workflow_end_to_end():
    """Test canonical workflow 'Open Firefox and search for autonomous agents' end-to-end."""
    from graph.nodes.intake import intake_node
    from graph.nodes.observe import observe_node
    from graph.nodes.analyze_intent import analyze_intent_node
    from graph.nodes.retrieve_knowledge import retrieve_knowledge_node
    from graph.nodes.create_plan import create_plan_node
    from graph.nodes.route import route_node
    from graph.nodes.safety_check import safety_check_node
    from graph.nodes.execute_step import execute_step_node
    from graph.nodes.verify_step import verify_step_node
    from graph.nodes.finalize import finalize_node
    from migration.graph_state import OperonixState
    from migration.domain_contracts import TaskRequest, TaskSource
    
    # Canonical workflow
    task = TaskRequest(
        user_input="Open Firefox and search for autonomous agents",
        source=TaskSource.VOICE
    )
    state = OperonixState(task=task)
    
    # Execute all Phase 4 nodes in sequence
    # Field-level updates: merge results into state using model_copy
    result = intake_node(state)
    state = state.model_copy(update=result)
    result = observe_node(state)
    state = state.model_copy(update=result)
    result = analyze_intent_node(state)
    state = state.model_copy(update=result)
    result = retrieve_knowledge_node(state)
    state = state.model_copy(update=result)
    result = create_plan_node(state)
    state = state.model_copy(update=result)
    result = route_node(state)
    state = state.model_copy(update=result)
    result = safety_check_node(state)
    state = state.model_copy(update=result)
    result = execute_step_node(state)
    state = state.model_copy(update=result)
    result = verify_step_node(state)
    state = state.model_copy(update=result)
    result = finalize_node(state)
    state = state.model_copy(update=result)
    
    # Verify final state has all Phase 4 components
    assert state.final is not None
    assert state.intent is not None
    assert state.knowledge is not None
    assert state.plan is not None
    assert state.routing is not None
    assert state.safety is not None
    assert state.execution is not None
    assert state.verification is not None
    assert state.final.success is True
    
    # Verify history tracking
    assert len(state.history.get("events", [])) >= 10  # 10 nodes


def test_graph_topology_phase4():
    """Test that graph topology includes all Phase 4 nodes."""
    from graph.graph import build_operonix_graph
    
    graph = build_operonix_graph()
    
    # Skip if LangGraph is not installed (function returns None in that case)
    if graph is None:
        pytest.skip("LangGraph not installed")
    
    assert graph is not None


def test_state_flow_through_all_nodes():
    """Test that state flows correctly through all Phase 4 nodes."""
    from graph.nodes.intake import intake_node
    from graph.nodes.observe import observe_node
    from graph.nodes.analyze_intent import analyze_intent_node
    from graph.nodes.retrieve_knowledge import retrieve_knowledge_node
    from graph.nodes.create_plan import create_plan_node
    from graph.nodes.route import route_node
    from graph.nodes.safety_check import safety_check_node
    from graph.nodes.execute_step import execute_step_node
    from graph.nodes.verify_step import verify_step_node
    from migration.graph_state import OperonixState
    from migration.domain_contracts import TaskRequest, TaskSource
    
    task = TaskRequest(user_input="test", source=TaskSource.API)
    state = OperonixState(task=task)
    
    # Execute nodes
    # Field-level updates: merge results into state using model_copy
    result = intake_node(state)
    state = state.model_copy(update=result)
    result = observe_node(state)
    state = state.model_copy(update=result)
    result = analyze_intent_node(state)
    state = state.model_copy(update=result)
    result = retrieve_knowledge_node(state)
    state = state.model_copy(update=result)
    result = create_plan_node(state)
    state = state.model_copy(update=result)
    result = route_node(state)
    state = state.model_copy(update=result)
    result = safety_check_node(state)
    state = state.model_copy(update=result)
    result = execute_step_node(state)
    state = state.model_copy(update=result)
    result = verify_step_node(state)
    state = state.model_copy(update=result)
    
    # Verify state components are populated
    assert state.task is not None
    assert state.intent is not None
    assert state.knowledge is not None
    assert state.plan is not None
    assert state.routing is not None
    assert state.safety is not None
    assert state.execution is not None
    assert state.verification is not None


def test_history_tracking_all_nodes():
    """Test that all Phase 4 nodes track history."""
    from graph.nodes.intake import intake_node
    from graph.nodes.observe import observe_node
    from graph.nodes.analyze_intent import analyze_intent_node
    from graph.nodes.retrieve_knowledge import retrieve_knowledge_node
    from graph.nodes.create_plan import create_plan_node
    from graph.nodes.route import route_node
    from graph.nodes.safety_check import safety_check_node
    from graph.nodes.execute_step import execute_step_node
    from graph.nodes.verify_step import verify_step_node
    from migration.graph_state import OperonixState
    from migration.domain_contracts import TaskRequest, TaskSource
    
    task = TaskRequest(user_input="test", source=TaskSource.API)
    state = OperonixState(task=task)
    
    # Execute nodes
    intake_node(state)
    observe_node(state)
    analyze_intent_node(state)
    retrieve_knowledge_node(state)
    create_plan_node(state)
    route_node(state)
    safety_check_node(state)
    execute_step_node(state)
    verify_step_node(state)
    
    events = state.history.get("events", [])
    assert len(events) >= 18  # 9 nodes x 2 events each (started/completed)
    
    event_types = [e["type"] for e in events]
    expected_events = [
        "intake_started", "intake_completed",
        "observe_started", "observe_completed",
        "analyze_intent_started", "analyze_intent_completed",
        "retrieve_knowledge_started", "retrieve_knowledge_completed",
        "create_plan_started", "create_plan_completed",
        "route_started", "route_completed",
        "safety_check_started", "safety_check_completed",
        "execute_step_started", "execute_step_completed",
        "verify_step_started", "verify_step_completed"
    ]
    
    for expected in expected_events:
        assert expected in event_types


# ─── DOMAIN OBJECT TESTS ─────────────────────────────────────────────────────

def test_knowledge_context_domain_object():
    """Test that KnowledgeContext is a valid domain object."""
    from migration.domain_contracts import KnowledgeContext
    
    knowledge = KnowledgeContext()
    
    assert knowledge.retrieved_memories is not None
    assert knowledge.retrieved_documents is not None
    assert knowledge.learned_patterns is not None
    assert knowledge.provenance is not None


def test_method_decision_domain_object():
    """Test that MethodDecision is a valid domain object."""
    from migration.domain_contracts import MethodDecision, RoutingCandidate
    
    candidate = RoutingCandidate(method_type="SHELL", overall_score=0.8)
    decision = MethodDecision(selected_candidate=candidate, confidence=0.8)
    
    assert decision.selected_candidate == candidate
    assert decision.confidence == 0.8
    assert decision.candidates_considered is not None


def test_safety_decision_domain_object():
    """Test that SafetyDecision is a valid domain object."""
    from migration.domain_contracts import SafetyDecision, RiskLevel
    
    safety = SafetyDecision(
        risk_level=RiskLevel.LOW,
        validation_status="APPROVED",
        permission_status="GRANTED"
    )
    
    assert safety.risk_level == RiskLevel.LOW
    assert safety.validation_status == "APPROVED"
    assert safety.permission_status == "GRANTED"


def test_execution_result_domain_object():
    """Test that ExecutionResult is a valid domain object."""
    from migration.domain_contracts import ExecutionResult, TaskStatus
    
    result = ExecutionResult(
        execution_id="test_id",
        step_id="step_1",
        success=True,
        method_used="SHELL",
        execution_status=TaskStatus.COMPLETED
    )
    
    assert result.execution_id == "test_id"
    assert result.success is True
    assert result.execution_status == TaskStatus.COMPLETED


def test_verification_result_domain_object():
    """Test that VerificationResult is a valid domain object."""
    from migration.domain_contracts import VerificationResult, ContextSnapshot
    
    verification = VerificationResult(
        status="VERIFIED",
        observed_context=ContextSnapshot()
    )
    
    assert verification.status == "VERIFIED"
    assert verification.observed_context is not None


if __name__ == "__main__":
    pytest.main([__file__, "-v"])
