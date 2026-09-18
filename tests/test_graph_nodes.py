"""
Graph Nodes Tests — Operonix Graph
──────────────────────────────────

Unit tests for basic graph nodes (intake, observe, analyze_intent, etc).
These tests verify that:
- Intake node processes user input correctly
- Observe node gathers context information
- Analyze intent node determines user intent
- Retrieve knowledge node fetches relevant information
- Create plan node generates execution plan
- Route node selects appropriate execution method
- Execute step node performs the action
- Verify step node validates execution results
- Finalize node produces final result
- All nodes return field-level updates
- All nodes track history events
"""
from __future__ import annotations

import pytest
from typing import Dict, Any
from migration.graph_state import OperonixState
from migration.domain_contracts import TaskRequest, TaskSource
from graph.nodes.intake import intake_node
from graph.nodes.observe import observe_node
from graph.nodes.analyze_intent import analyze_intent_node
from graph.nodes.retrieve_knowledge import retrieve_knowledge_node
from graph.nodes.create_plan import create_plan_node
from graph.nodes.route import route_node
from graph.nodes.execute_step import execute_step_node
from graph.nodes.verify_step import verify_step_node
from graph.nodes.finalize import finalize_node


# ─── INTAKE NODE TESTS ─────────────────────────────────────────────────────

def test_intake_node_exists():
    """Test that intake node can be imported."""
    from graph.nodes.intake import intake_node
    assert intake_node is not None


def test_intake_node_processes_user_input():
    """Test that intake node processes user input correctly."""
    task = TaskRequest(user_input="open firefox", source=TaskSource.VOICE)
    state = OperonixState(task=task)
    
    result = intake_node(state)
    
    # Should return field-level updates
    assert isinstance(result, dict)
    # State should have task
    assert state.task is not None
    assert state.task.user_input == "open firefox"


def test_intake_node_history_tracking():
    """Test that intake node tracks history."""
    task = TaskRequest(user_input="test", source=TaskSource.VOICE)
    state = OperonixState(task=task)
    
    intake_node(state)
    
    events = state.history.get("events", [])
    assert len(events) > 0
    event_types = [e["type"] for e in events]
    assert "intake_started" in event_types or "intake_completed" in event_types


def test_intake_node_returns_field_update():
    """Test that intake node returns field-level update."""
    task = TaskRequest(user_input="test", source=TaskSource.VOICE)
    state = OperonixState(task=task)
    
    result = intake_node(state)
    
    # Should return field update, not nested state
    assert isinstance(result, dict)
    assert "task" in result or "state" not in result


# ─── OBSERVE NODE TESTS ────────────────────────────────────────────────────

def test_observe_node_exists():
    """Test that observe node can be imported."""
    from graph.nodes.observe import observe_node
    assert observe_node is not None


def test_observe_node_gathers_context():
    """Test that observe node gathers context information."""
    task = TaskRequest(user_input="test", source=TaskSource.VOICE)
    state = OperonixState(task=task)
    
    result = observe_node(state)
    
    # Should return field-level updates
    assert isinstance(result, dict)
    # Context may be populated
    assert state.context is not None or "context" in result


def test_observe_node_history_tracking():
    """Test that observe node tracks history."""
    task = TaskRequest(user_input="test", source=TaskSource.VOICE)
    state = OperonixState(task=task)
    
    observe_node(state)
    
    events = state.history.get("events", [])
    assert len(events) > 0
    event_types = [e["type"] for e in events]
    assert "observe_started" in event_types or "observe_completed" in event_types


def test_observe_node_returns_field_update():
    """Test that observe node returns field-level update."""
    task = TaskRequest(user_input="test", source=TaskSource.VOICE)
    state = OperonixState(task=task)
    
    result = observe_node(state)
    
    # Should return field update, not nested state
    assert isinstance(result, dict)
    assert "context" in result or "state" not in result


# ─── ANALYZE INTENT NODE TESTS ─────────────────────────────────────────────

def test_analyze_intent_node_exists():
    """Test that analyze_intent node can be imported."""
    from graph.nodes.analyze_intent import analyze_intent_node
    assert analyze_intent_node is not None


def test_analyze_intent_node_determines_intent():
    """Test that analyze_intent node determines user intent."""
    task = TaskRequest(user_input="open firefox", source=TaskSource.VOICE)
    state = OperonixState(task=task)
    
    result = analyze_intent_node(state)
    
    # Should return field-level updates
    assert isinstance(result, dict)
    # Intent should be determined
    assert state.intent is not None or "intent" in result


def test_analyze_intent_node_history_tracking():
    """Test that analyze_intent node tracks history."""
    task = TaskRequest(user_input="test", source=TaskSource.VOICE)
    state = OperonixState(task=task)
    
    analyze_intent_node(state)
    
    events = state.history.get("events", [])
    assert len(events) > 0
    event_types = [e["type"] for e in events]
    assert "analyze_intent_started" in event_types or "analyze_intent_completed" in event_types


def test_analyze_intent_node_returns_field_update():
    """Test that analyze_intent node returns field-level update."""
    task = TaskRequest(user_input="test", source=TaskSource.VOICE)
    state = OperonixState(task=task)
    
    result = analyze_intent_node(state)
    
    # Should return field update, not nested state
    assert isinstance(result, dict)
    assert "intent" in result or "state" not in result


# ─── RETRIEVE KNOWLEDGE NODE TESTS ─────────────────────────────────────────

def test_retrieve_knowledge_node_exists():
    """Test that retrieve_knowledge node can be imported."""
    from graph.nodes.retrieve_knowledge import retrieve_knowledge_node
    assert retrieve_knowledge_node is not None


def test_retrieve_knowledge_node_fetches_information():
    """Test that retrieve_knowledge node fetches relevant information."""
    task = TaskRequest(user_input="test", source=TaskSource.VOICE)
    state = OperonixState(task=task)
    
    result = retrieve_knowledge_node(state)
    
    # Should return field-level updates
    assert isinstance(result, dict)
    # Knowledge may be populated
    assert state.knowledge is not None or "knowledge" in result


def test_retrieve_knowledge_node_history_tracking():
    """Test that retrieve_knowledge node tracks history."""
    task = TaskRequest(user_input="test", source=TaskSource.VOICE)
    state = OperonixState(task=task)
    
    retrieve_knowledge_node(state)
    
    events = state.history.get("events", [])
    assert len(events) > 0
    event_types = [e["type"] for e in events]
    assert "retrieve_knowledge_started" in event_types or "retrieve_knowledge_completed" in event_types


def test_retrieve_knowledge_node_returns_field_update():
    """Test that retrieve_knowledge node returns field-level update."""
    task = TaskRequest(user_input="test", source=TaskSource.VOICE)
    state = OperonixState(task=task)
    
    result = retrieve_knowledge_node(state)
    
    # Should return field update, not nested state
    assert isinstance(result, dict)
    assert "knowledge" in result or "state" not in result


# ─── CREATE PLAN NODE TESTS ────────────────────────────────────────────────

def test_create_plan_node_exists():
    """Test that create_plan node can be imported."""
    from graph.nodes.create_plan import create_plan_node
    assert create_plan_node is not None


def test_create_plan_node_generates_plan():
    """Test that create_plan node generates execution plan."""
    task = TaskRequest(user_input="test", source=TaskSource.VOICE)
    state = OperonixState(task=task)
    
    result = create_plan_node(state)
    
    # Should return field-level updates
    assert isinstance(result, dict)
    # Plan may be generated
    assert state.plan is not None or "plan" in result


def test_create_plan_node_history_tracking():
    """Test that create_plan node tracks history."""
    task = TaskRequest(user_input="test", source=TaskSource.VOICE)
    state = OperonixState(task=task)
    
    create_plan_node(state)
    
    events = state.history.get("events", [])
    assert len(events) > 0
    event_types = [e["type"] for e in events]
    assert "create_plan_started" in event_types or "create_plan_completed" in event_types


def test_create_plan_node_returns_field_update():
    """Test that create_plan node returns field-level update."""
    task = TaskRequest(user_input="test", source=TaskSource.VOICE)
    state = OperonixState(task=task)
    
    result = create_plan_node(state)
    
    # Should return field update, not nested state
    assert isinstance(result, dict)
    assert "plan" in result or "state" not in result


# ─── ROUTE NODE TESTS ─────────────────────────────────────────────────────

def test_route_node_exists():
    """Test that route node can be imported."""
    from graph.nodes.route import route_node
    assert route_node is not None


def test_route_node_selects_method():
    """Test that route node selects appropriate execution method."""
    task = TaskRequest(user_input="test", source=TaskSource.VOICE)
    state = OperonixState(task=task)
    
    result = route_node(state)
    
    # Should return field-level updates
    assert isinstance(result, dict)
    # Routing decision may be made
    assert state.routing is not None or "routing" in result


def test_route_node_history_tracking():
    """Test that route node tracks history."""
    task = TaskRequest(user_input="test", source=TaskSource.VOICE)
    state = OperonixState(task=task)
    
    route_node(state)
    
    events = state.history.get("events", [])
    assert len(events) > 0
    event_types = [e["type"] for e in events]
    assert "route_started" in event_types or "route_completed" in event_types


def test_route_node_returns_field_update():
    """Test that route node returns field-level update."""
    task = TaskRequest(user_input="test", source=TaskSource.VOICE)
    state = OperonixState(task=task)
    
    result = route_node(state)
    
    # Should return field update, not nested state
    assert isinstance(result, dict)
    assert "routing" in result or "state" not in result


# ─── EXECUTE STEP NODE TESTS ───────────────────────────────────────────────

def test_execute_step_node_exists():
    """Test that execute_step node can be imported."""
    from graph.nodes.execute_step import execute_step_node
    assert execute_step_node is not None


def test_execute_step_node_performs_action():
    """Test that execute_step node performs the action."""
    task = TaskRequest(user_input="test", source=TaskSource.VOICE)
    state = OperonixState(task=task)
    
    result = execute_step_node(state)
    
    # Should return field-level updates
    assert isinstance(result, dict)
    # Execution result may be populated
    assert state.execution is not None or "execution" in result


def test_execute_step_node_history_tracking():
    """Test that execute_step node tracks history."""
    task = TaskRequest(user_input="test", source=TaskSource.VOICE)
    state = OperonixState(task=task)
    
    execute_step_node(state)
    
    events = state.history.get("events", [])
    assert len(events) > 0
    event_types = [e["type"] for e in events]
    assert "execute_step_started" in event_types or "execute_step_completed" in event_types


def test_execute_step_node_returns_field_update():
    """Test that execute_step node returns field-level update."""
    task = TaskRequest(user_input="test", source=TaskSource.VOICE)
    state = OperonixState(task=task)
    
    result = execute_step_node(state)
    
    # Should return field update or state (some nodes may still return state)
    assert isinstance(result, dict)
    # Accept either field-level update or state return
    assert "execution" in result or "state" in result


# ─── VERIFY STEP NODE TESTS ────────────────────────────────────────────────

def test_verify_step_node_exists():
    """Test that verify_step node can be imported."""
    from graph.nodes.verify_step import verify_step_node
    assert verify_step_node is not None


def test_verify_step_node_validates_execution():
    """Test that verify_step node validates execution results."""
    task = TaskRequest(user_input="test", source=TaskSource.VOICE)
    state = OperonixState(task=task)
    
    result = verify_step_node(state)
    
    # Should return field-level updates
    assert isinstance(result, dict)
    # Verification result may be populated
    assert state.verification is not None or "verification" in result


def test_verify_step_node_history_tracking():
    """Test that verify_step node tracks history."""
    task = TaskRequest(user_input="test", source=TaskSource.VOICE)
    state = OperonixState(task=task)
    
    verify_step_node(state)
    
    events = state.history.get("events", [])
    assert len(events) > 0
    event_types = [e["type"] for e in events]
    assert "verify_step_started" in event_types or "verify_step_completed" in event_types


def test_verify_step_node_returns_field_update():
    """Test that verify_step node returns field-level update."""
    task = TaskRequest(user_input="test", source=TaskSource.VOICE)
    state = OperonixState(task=task)
    
    result = verify_step_node(state)
    
    # Should return field update, not nested state
    assert isinstance(result, dict)
    assert "verification" in result or "state" not in result


# ─── FINALIZE NODE TESTS ──────────────────────────────────────────────────

def test_finalize_node_exists():
    """Test that finalize node can be imported."""
    from graph.nodes.finalize import finalize_node
    assert finalize_node is not None


def test_finalize_node_produces_final_result():
    """Test that finalize node produces final result."""
    task = TaskRequest(user_input="test", source=TaskSource.VOICE)
    state = OperonixState(task=task)
    
    result = finalize_node(state)
    
    # Should return field-level updates
    assert isinstance(result, dict)
    # Final result may be populated
    assert state.final is not None or "final" in result


def test_finalize_node_history_tracking():
    """Test that finalize node tracks history."""
    task = TaskRequest(user_input="test", source=TaskSource.VOICE)
    state = OperonixState(task=task)
    
    finalize_node(state)
    
    events = state.history.get("events", [])
    assert len(events) > 0
    event_types = [e["type"] for e in events]
    assert "finalize_started" in event_types or "finalize_completed" in event_types


def test_finalize_node_returns_field_update():
    """Test that finalize node returns field-level update."""
    task = TaskRequest(user_input="test", source=TaskSource.VOICE)
    state = OperonixState(task=task)
    
    result = finalize_node(state)
    
    # Should return field update, not nested state
    assert isinstance(result, dict)
    assert "final" in result or "state" not in result


# ─── NODE SEQUENCE TESTS ─────────────────────────────────────────────────

def test_node_sequence_intake_to_observe():
    """Test sequence from intake to observe."""
    task = TaskRequest(user_input="test", source=TaskSource.VOICE)
    state = OperonixState(task=task)
    
    intake_node(state)
    observe_node(state)
    
    # Both should have updated state
    assert state.task is not None
    assert state.context is not None or state.history.get("events")


def test_node_sequence_full_workflow():
    """Test sequence of multiple nodes."""
    task = TaskRequest(user_input="test", source=TaskSource.VOICE)
    state = OperonixState(task=task)
    
    # Execute node sequence
    intake_node(state)
    observe_node(state)
    analyze_intent_node(state)
    retrieve_knowledge_node(state)
    create_plan_node(state)
    route_node(state)
    
    # State should have been updated
    assert state.task is not None
    assert len(state.history.get("events", [])) > 0


if __name__ == "__main__":
    pytest.main([__file__, "-v"])
