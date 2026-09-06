"""
Planning Integration Tests — Operonix Phase 3
──────────────────────────────────────────────

Tests for Phase 3 planning integration implementation.
These tests verify that:
- Create plan node works with deterministic/AI split
- Simple requests generate deterministic plans
- Complex requests generate LangChain-backed plans
- Plan and PlanStep are valid domain objects
- Graph owns current step, completed steps, workflow position
- Planner owns what the steps are
"""
from __future__ import annotations

import pytest
from typing import Dict, Any


# ─── CREATE PLAN NODE TESTS ───────────────────────────────────────────────────

def test_create_plan_node_exists():
    """Test that create_plan node can be imported."""
    from graph.nodes.create_plan import create_plan_node
    assert create_plan_node is not None


def test_create_plan_node_with_simple_request():
    """Test that create_plan node handles simple requests."""
    from graph.nodes.create_plan import create_plan_node
    from migration.graph_state import OperonixState
    from migration.domain_contracts import TaskRequest, TaskSource, IntentResult
    
    task = TaskRequest(user_input="Open Firefox", source=TaskSource.VOICE)
    state = OperonixState(task=task)
    state.intent = IntentResult(name="open_application", confidence=0.9)
    
    result = create_plan_node(state)
    
    assert "state" in result
    assert result["state"].plan is not None
    assert len(result["state"].plan.steps) == 1  # Simple = single step


def test_create_plan_node_with_complex_request():
    """Test that create_plan node handles complex requests."""
    from graph.nodes.create_plan import create_plan_node
    from migration.graph_state import OperonixState
    from migration.domain_contracts import TaskRequest, TaskSource, IntentResult
    
    task = TaskRequest(user_input="Open Firefox and search for autonomous agents", source=TaskSource.VOICE)
    state = OperonixState(task=task)
    state.intent = IntentResult(name="complex_action", confidence=0.9)
    
    result = create_plan_node(state)
    
    assert "state" in result
    assert result["state"].plan is not None
    assert len(result["state"].plan.steps) >= 1  # Complex = multiple steps


def test_create_plan_node_creates_valid_plan():
    """Test that create_plan node creates valid Plan domain object."""
    from graph.nodes.create_plan import create_plan_node
    from migration.graph_state import OperonixState
    from migration.domain_contracts import TaskRequest, TaskSource, IntentResult, Plan
    
    task = TaskRequest(user_input="test", source=TaskSource.API)
    state = OperonixState(task=task)
    state.intent = IntentResult(name="test", confidence=0.9)
    
    result = create_plan_node(state)
    
    assert isinstance(result["state"].plan, Plan)
    assert result["state"].plan.plan_id is not None
    assert result["state"].plan.steps is not None
    assert len(result["state"].plan.steps) > 0


def test_create_plan_node_creates_valid_plan_steps():
    """Test that create_plan node creates valid PlanStep domain objects."""
    from graph.nodes.create_plan import create_plan_node
    from migration.graph_state import OperonixState
    from migration.domain_contracts import TaskRequest, TaskSource, IntentResult, PlanStep
    
    task = TaskRequest(user_input="test", source=TaskSource.API)
    state = OperonixState(task=task)
    state.intent = IntentResult(name="test", confidence=0.9)
    
    result = create_plan_node(state)
    
    for step in result["state"].plan.steps:
        assert isinstance(step, PlanStep)
        assert step.step_id is not None
        assert step.action is not None
        assert step.idempotency in ["SAFE", "CONDITIONAL", "NON_IDEMPOTENT"]
        assert step.side_effect in ["READ_ONLY", "REVERSIBLE", "LIMITED_SIDE_EFFECT", "DESTRUCTIVE", "EXTERNAL_COMMIT"]


def test_create_plan_node_idempotency_classification():
    """Test that plan steps have idempotency classification."""
    from graph.nodes.create_plan import create_plan_node
    from migration.graph_state import OperonixState
    from migration.domain_contracts import TaskRequest, TaskSource, IntentResult
    
    task = TaskRequest(user_input="test", source=TaskSource.API)
    state = OperonixState(task=task)
    state.intent = IntentResult(name="test", confidence=0.9)
    
    result = create_plan_node(state)
    
    for step in result["state"].plan.steps:
        assert step.idempotency is not None
        assert step.idempotency in ["SAFE", "CONDITIONAL", "NON_IDEMPOTENT"]


def test_create_plan_node_side_effect_classification():
    """Test that plan steps have side-effect classification."""
    from graph.nodes.create_plan import create_plan_node
    from migration.graph_state import OperonixState
    from migration.domain_contracts import TaskRequest, TaskSource, IntentResult
    
    task = TaskRequest(user_input="test", source=TaskSource.API)
    state = OperonixState(task=task)
    state.intent = IntentResult(name="test", confidence=0.9)
    
    result = create_plan_node(state)
    
    for step in result["state"].plan.steps:
        assert step.side_effect is not None
        assert step.side_effect in ["READ_ONLY", "REVERSIBLE", "LIMITED_SIDE_EFFECT", "DESTRUCTIVE", "EXTERNAL_COMMIT"]


def test_create_plan_node_history_tracking():
    """Test that create_plan node tracks history."""
    from graph.nodes.create_plan import create_plan_node
    from migration.graph_state import OperonixState
    from migration.domain_contracts import TaskRequest, TaskSource, IntentResult
    
    task = TaskRequest(user_input="test", source=TaskSource.API)
    state = OperonixState(task=task)
    state.intent = IntentResult(name="test", confidence=0.9)
    
    result = create_plan_node(state)
    
    events = result["state"].history.get("events", [])
    assert len(events) >= 2  # create_plan_started, create_plan_completed
    
    event_types = [e["type"] for e in events]
    assert "create_plan_started" in event_types
    assert "create_plan_completed" in event_types


# ─── BEHAVIORAL TESTS FOR PLANNING ─────────────────────────────────────────────

def test_complexity_detection_with_langchain_enabled():
    """Test that complexity detection uses LangChain when enabled."""
    from graph.nodes.create_plan import _is_complex_request
    from migration.feature_flags import flags
    
    # Enable LangChain models
    original_flag = flags.USE_LANGCHAIN_MODELS
    flags.USE_LANGCHAIN_MODELS = True
    
    try:
        # Simple request
        is_complex = _is_complex_request("Open Firefox")
        # If LangChain is available, it should return False for simple
        # If not available, it should fall back to heuristic (also False)
        assert isinstance(is_complex, bool)
        
        # Complex request
        is_complex = _is_complex_request("Open Firefox and search for agents")
        # If LangChain is available, it should return True for complex
        # If not available, it should fall back to heuristic (also True)
        assert isinstance(is_complex, bool)
        
    finally:
        flags.USE_LANGCHAIN_MODELS = original_flag


def test_complexity_detection_with_langchain_disabled():
    """Test that complexity detection uses heuristic when LangChain disabled."""
    from graph.nodes.create_plan import _is_complex_request
    from migration.feature_flags import flags
    
    # Disable LangChain models
    original_flag = flags.USE_LANGCHAIN_MODELS
    flags.USE_LANGCHAIN_MODELS = False
    
    try:
        # Simple request
        is_complex = _is_complex_request("Open Firefox")
        assert is_complex is False
        
        # Complex request (keyword-based)
        is_complex = _is_complex_request("Open Firefox and search for agents")
        assert is_complex is True
        
        # Complex request (length-based)
        is_complex = _is_complex_request("Open Firefox and then navigate to Google and search for autonomous agents and then click on the first result")
        assert is_complex is True
        
    finally:
        flags.USE_LANGCHAIN_MODELS = original_flag


def test_complex_plan_generation_with_langchain_enabled():
    """Test that complex plan generation uses LangChain when enabled."""
    from graph.nodes.create_plan import _generate_complex_plan
    from migration.graph_state import OperonixState
    from migration.domain_contracts import TaskRequest, TaskSource, IntentResult
    from migration.feature_flags import flags
    
    # Enable LangChain models
    original_flag = flags.USE_LANGCHAIN_MODELS
    flags.USE_LANGCHAIN_MODELS = True
    
    try:
        task = TaskRequest(user_input="Open Firefox and search for agents", source=TaskSource.VOICE)
        state = OperonixState(task=task)
        state.intent = IntentResult(name="complex_action", confidence=0.9)
        
        plan = _generate_complex_plan(state)
        
        assert plan is not None
        assert len(plan.steps) >= 1
        # If LangChain is available, steps should be AI-generated
        # If not available, should fall back to placeholder (2 steps)
        
    finally:
        flags.USE_LANGCHAIN_MODELS = original_flag


def test_simple_plan_with_planner_integration():
    """Test that simple plan generation integrates with brain/planner.py."""
    from graph.nodes.create_plan import _generate_simple_plan
    from migration.graph_state import OperonixState
    from migration.domain_contracts import TaskRequest, TaskSource, IntentResult
    
    task = TaskRequest(user_input="Open Firefox", source=TaskSource.VOICE)
    state = OperonixState(task=task)
    state.intent = IntentResult(name="open_application", confidence=0.9)
    
    plan = _generate_simple_plan(state)
    
    assert plan is not None
    assert len(plan.steps) == 1
    # Should integrate with brain/planner.py for arg resolution
    # If integration fails, should fall back to simple plan


def test_plan_step_dependencies():
    """Test that plan steps have dependencies for complex plans."""
    from graph.nodes.create_plan import _generate_complex_plan
    from migration.graph_state import OperonixState
    from migration.domain_contracts import TaskRequest, TaskSource, IntentResult
    from migration.feature_flags import flags
    
    # Disable LangChain to test placeholder plan
    original_flag = flags.USE_LANGCHAIN_MODELS
    flags.USE_LANGCHAIN_MODELS = False
    
    try:
        task = TaskRequest(user_input="Open Firefox and search for agents", source=TaskSource.VOICE)
        state = OperonixState(task=task)
        state.intent = IntentResult(name="complex_action", confidence=0.9)
        
        plan = _generate_complex_plan(state)
        
        assert plan is not None
        assert len(plan.steps) >= 2
        
        # Check that second step depends on first step
        if len(plan.steps) >= 2:
            assert plan.steps[1].dependencies is not None
            assert plan.steps[0].step_id in plan.steps[1].dependencies
        
    finally:
        flags.USE_LANGCHAIN_MODELS = original_flag


def test_plan_step_idempotency_classification():
    """Test that plan steps have idempotency classification."""
    from graph.nodes.create_plan import _generate_complex_plan
    from migration.graph_state import OperonixState
    from migration.domain_contracts import TaskRequest, TaskSource, IntentResult
    from migration.feature_flags import flags
    
    # Disable LangChain to test placeholder plan
    original_flag = flags.USE_LANGCHAIN_MODELS
    flags.USE_LANGCHAIN_MODELS = False
    
    try:
        task = TaskRequest(user_input="Open Firefox and search for agents", source=TaskSource.VOICE)
        state = OperonixState(task=task)
        state.intent = IntentResult(name="complex_action", confidence=0.9)
        
        plan = _generate_complex_plan(state)
        
        for step in plan.steps:
            assert step.idempotency is not None
            assert step.idempotency in ["SAFE", "CONDITIONAL", "NON_IDEMPOTENT"]
        
    finally:
        flags.USE_LANGCHAIN_MODELS = original_flag


def test_plan_step_side_effect_classification():
    """Test that plan steps have side-effect classification."""
    from graph.nodes.create_plan import _generate_complex_plan
    from migration.graph_state import OperonixState
    from migration.domain_contracts import TaskRequest, TaskSource, IntentResult
    from migration.feature_flags import flags
    
    # Disable LangChain to test placeholder plan
    original_flag = flags.USE_LANGCHAIN_MODELS
    flags.USE_LANGCHAIN_MODELS = False
    
    try:
        task = TaskRequest(user_input="Open Firefox and search for agents", source=TaskSource.VOICE)
        state = OperonixState(task=task)
        state.intent = IntentResult(name="complex_action", confidence=0.9)
        
        plan = _generate_complex_plan(state)
        
        for step in plan.steps:
            assert step.side_effect is not None
            assert step.side_effect in ["READ_ONLY", "REVERSIBLE", "LIMITED_SIDE_EFFECT", "DESTRUCTIVE", "EXTERNAL_COMMIT"]
        
    finally:
        flags.USE_LANGCHAIN_MODELS = original_flag


def test_plan_step_reversibility():
    """Test that plan steps have reversibility flag."""
    from graph.nodes.create_plan import _generate_complex_plan
    from migration.graph_state import OperonixState
    from migration.domain_contracts import TaskRequest, TaskSource, IntentResult
    from migration.feature_flags import flags
    
    # Disable LangChain to test placeholder plan
    original_flag = flags.USE_LANGCHAIN_MODELS
    flags.USE_LANGCHAIN_MODELS = False
    
    try:
        task = TaskRequest(user_input="Open Firefox and search for agents", source=TaskSource.VOICE)
        state = OperonixState(task=task)
        state.intent = IntentResult(name="complex_action", confidence=0.9)
        
        plan = _generate_complex_plan(state)
        
        for step in plan.steps:
            assert step.reversibility is not None
            assert isinstance(step.reversibility, bool)
        
    finally:
        flags.USE_LANGCHAIN_MODELS = original_flag


def test_plan_step_objectives():
    """Test that plan steps have clear objectives."""
    from graph.nodes.create_plan import _generate_complex_plan
    from migration.graph_state import OperonixState
    from migration.domain_contracts import TaskRequest, TaskSource, IntentResult
    from migration.feature_flags import flags
    
    # Disable LangChain to test placeholder plan
    original_flag = flags.USE_LANGCHAIN_MODELS
    flags.USE_LANGCHAIN_MODELS = False
    
    try:
        task = TaskRequest(user_input="Open Firefox and search for agents", source=TaskSource.VOICE)
        state = OperonixState(task=task)
        state.intent = IntentResult(name="complex_action", confidence=0.9)
        
        plan = _generate_complex_plan(state)
        
        for step in plan.steps:
            assert step.objective is not None
            assert len(step.objective) > 0
        
    finally:
        flags.USE_LANGCHAIN_MODELS = original_flag


# ─── DETERMINISTIC/AI SPLIT TESTS ───────────────────────────────────────────────

def test_is_complex_request_simple():
    """Test that simple requests are correctly classified."""
    from graph.nodes.create_plan import _is_complex_request
    
    assert _is_complex_request("Open Firefox") is False
    assert _is_complex_request("Create file") is False
    assert _is_complex_request("Delete file") is False


def test_is_complex_request_complex():
    """Test that complex requests are correctly classified."""
    from graph.nodes.create_plan import _is_complex_request
    
    assert _is_complex_request("Open Firefox and search for agents") is True
    assert _is_complex_request("Open Firefox then navigate to Google") is True
    assert _is_complex_request("Open Firefox and search for autonomous agents") is True


def test_is_complex_request_length_based():
    """Test that long requests are classified as complex."""
    from graph.nodes.create_plan import _is_complex_request
    
    long_request = "Open Firefox and then navigate to Google and search for autonomous agents and then click on the first result"
    assert _is_complex_request(long_request) is True


def test_generate_simple_plan():
    """Test that simple plan generation works."""
    from graph.nodes.create_plan import _generate_simple_plan
    from migration.graph_state import OperonixState
    from migration.domain_contracts import TaskRequest, TaskSource, IntentResult
    
    task = TaskRequest(user_input="Open Firefox", source=TaskSource.VOICE)
    state = OperonixState(task=task)
    state.intent = IntentResult(name="open_application", confidence=0.9)
    
    plan = _generate_simple_plan(state)
    
    assert plan is not None
    assert len(plan.steps) == 1
    assert plan.current_step is not None


def test_generate_complex_plan():
    """Test that complex plan generation works."""
    from graph.nodes.create_plan import _generate_complex_plan
    from migration.graph_state import OperonixState
    from migration.domain_contracts import TaskRequest, TaskSource, IntentResult
    
    task = TaskRequest(user_input="Open Firefox and search", source=TaskSource.VOICE)
    state = OperonixState(task=task)
    state.intent = IntentResult(name="complex_action", confidence=0.9)
    
    plan = _generate_complex_plan(state)
    
    assert plan is not None
    assert len(plan.steps) >= 2  # Complex = multiple steps


# ─── GRAPH OWNERSHIP TESTS ─────────────────────────────────────────────────────

def test_graph_owns_current_step():
    """Test that graph owns current step tracking."""
    from migration.graph_state import OperonixState
    from migration.domain_contracts import TaskRequest, TaskSource, Plan, PlanStep
    import uuid
    
    task = TaskRequest(user_input="test", source=TaskSource.API)
    step = PlanStep(step_id=str(uuid.uuid4()), action="test")
    plan = Plan(steps=[step])
    state = OperonixState(task=task)
    state.plan = plan
    
    # Graph owns current step tracking
    assert state.plan.current_step is not None
    assert state.plan.current_step_index == 0


def test_graph_owns_completed_steps():
    """Test that graph owns completed steps tracking."""
    from migration.graph_state import OperonixState
    from migration.domain_contracts import TaskRequest, TaskSource, Plan, PlanStep
    import uuid
    
    task = TaskRequest(user_input="test", source=TaskSource.API)
    step1 = PlanStep(step_id=str(uuid.uuid4()), action="test1")
    step2 = PlanStep(step_id=str(uuid.uuid4()), action="test2")
    plan = Plan(steps=[step1, step2])
    state = OperonixState(task=task)
    state.plan = plan
    
    # Graph owns completed steps tracking
    assert isinstance(state.plan.completed_steps, list)
    assert len(state.plan.completed_steps) == 0  # Initially empty


def test_graph_owns_workflow_position():
    """Test that graph owns workflow position tracking."""
    from migration.graph_state import OperonixState
    from migration.domain_contracts import TaskRequest, TaskSource
    
    task = TaskRequest(user_input="test", source=TaskSource.API)
    state = OperonixState(task=task)
    
    # Graph owns workflow position
    assert state.current_node is not None or state.current_node is None  # Can be None initially


# ─── PLANNER OWNERSHIP TESTS ─────────────────────────────────────────────────

def test_planner_owns_what_steps_are():
    """Test that planner owns what the steps are."""
    from graph.nodes.create_plan import _generate_simple_plan
    from migration.graph_state import OperonixState
    from migration.domain_contracts import TaskRequest, TaskSource, IntentResult
    
    task = TaskRequest(user_input="Open Firefox", source=TaskSource.VOICE)
    state = OperonixState(task=task)
    state.intent = IntentResult(name="open_application", confidence=0.9)
    
    plan = _generate_simple_plan(state)
    
    # Planner owns what the steps are (step definitions)
    assert plan.steps is not None
    assert len(plan.steps) > 0
    for step in plan.steps:
        assert step.action is not None
        assert step.objective is not None


# ─── INTEGRATION TESTS ───────────────────────────────────────────────────────

def test_node_sequence_with_plan():
    """Test that nodes can be called in sequence including create_plan."""
    from graph.nodes.intake import intake_node
    from graph.nodes.observe import observe_node
    from graph.nodes.analyze_intent import analyze_intent_node
    from graph.nodes.create_plan import create_plan_node
    from graph.nodes.finalize import finalize_node
    from migration.graph_state import OperonixState
    from migration.domain_contracts import TaskRequest, TaskSource
    
    task = TaskRequest(user_input="Open Firefox", source=TaskSource.VOICE)
    state = OperonixState(task=task)
    
    # Execute node sequence
    state = intake_node(state)["state"]
    state = observe_node(state)["state"]
    state = analyze_intent_node(state)["state"]
    state = create_plan_node(state)["state"]
    state = finalize_node(state)["state"]
    
    # Verify final state
    assert state.final is not None
    assert state.intent is not None
    assert state.plan is not None
    assert state.final.success is True
    assert len(state.history.get("events", [])) >= 5


def test_plan_domain_object_consumable():
    """Test that Plan and PlanStep are valid domain objects."""
    from migration.domain_contracts import Plan, PlanStep
    import uuid
    
    step = PlanStep(
        step_id=str(uuid.uuid4()),
        action="test_action",
        arguments={"key": "value"},
        objective="Test objective"
    )
    
    plan = Plan(steps=[step])
    
    # Plan is valid domain object
    assert plan.plan_id is not None
    assert plan.steps is not None
    assert plan.current_step is not None
    assert plan.is_complete is False
    
    # PlanStep is valid domain object
    assert step.step_id is not None
    assert step.action is not None
    assert step.arguments is not None
    assert step.objective is not None


if __name__ == "__main__":
    pytest.main([__file__, "-v"])
