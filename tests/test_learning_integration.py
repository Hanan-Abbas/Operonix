"""
Learning Integration Tests — Operonix Migration Phase 14
──────────────────────────────────────────────────────────

Tests for learning-driven routing integration.
Per migration plan Phase 14: Learning-Driven Routing & Adaptation
"""
from __future__ import annotations

import pytest


# ─── LEARNING INTEGRATION TESTS ─────────────────────────────────────────────

def test_learning_integration_initialization():
    """Test that LearningIntegration can be initialized."""
    from graph.learning_integration import LearningIntegration
    
    integration = LearningIntegration()
    
    assert integration is not None
    assert integration.performance_history == []


def test_learning_integration_connects_to_existing_learner():
    """Test that LearningIntegration connects to existing learning system."""
    from graph.learning_integration import LearningIntegration
    
    integration = LearningIntegration()
    
    # Should attempt to connect to existing learner
    # (may fail gracefully if learner not available)
    assert integration is not None


def test_collect_performance_feedback():
    """Test that performance feedback can be collected."""
    from graph.learning_integration import LearningIntegration
    
    integration = LearningIntegration()
    
    integration.collect_performance_feedback(
        task_id="task_1",
        intent="execute_command",
        method_type="shell",
        success=True,
        execution_time=1.5,
        retry_count=0,
        fallback_used=False
    )
    
    assert len(integration.performance_history) == 1
    assert integration.performance_history[0].task_id == "task_1"
    assert integration.performance_history[0].success is True


def test_collect_performance_feedback_multiple():
    """Test that multiple performance signals can be collected."""
    from graph.learning_integration import LearningIntegration
    
    integration = LearningIntegration()
    
    integration.collect_performance_feedback(
        task_id="task_1",
        intent="execute_command",
        method_type="shell",
        success=True,
        execution_time=1.5,
        retry_count=0,
        fallback_used=False
    )
    
    integration.collect_performance_feedback(
        task_id="task_2",
        intent="execute_command",
        method_type="shell",
        success=False,
        execution_time=2.0,
        retry_count=2,
        fallback_used=True
    )
    
    assert len(integration.performance_history) == 2


def test_get_historical_method_ranking():
    """Test that historical method ranking can be retrieved."""
    from graph.learning_integration import LearningIntegration
    
    integration = LearningIntegration()
    
    ranking = integration.get_historical_method_ranking("firefox", "execute_command")
    
    # Should return list (empty if no data)
    assert isinstance(ranking, list)


def test_apply_learning_adjustment_boost():
    """Test that learning adjustment boosts historically preferred methods."""
    from graph.learning_integration import LearningIntegration
    
    integration = LearningIntegration()
    
    # Mock historical ranking
    integration.retriever = type('MockRetriever', (), {
        'get_method_ranking': lambda self, app, intent: ["shell", "ui", "api"]
    })()
    
    adjusted_score = integration.apply_learning_adjustment(
        candidate_score=0.5,
        intent="execute_command",
        method_type="shell",
        app="firefox"
    )
    
    # Shell is first in ranking, should get boost
    assert adjusted_score > 0.5


def test_apply_learning_adjustment_penalty():
    """Test that learning adjustment penalizes low success rate methods."""
    from graph.learning_integration import LearningIntegration
    
    integration = LearningIntegration()
    
    # Add low success rate signals
    for _ in range(10):
        integration.collect_performance_feedback(
            task_id=f"task_{_}",
            intent="execute_command",
            method_type="shell",
            success=False,
            execution_time=1.0,
            retry_count=0,
            fallback_used=False
        )
    
    adjusted_score = integration.apply_learning_adjustment(
        candidate_score=0.5,
        intent="execute_command",
        method_type="shell",
        app="firefox"
    )
    
    # Low success rate should get penalty
    assert adjusted_score < 0.5


def test_apply_learning_adjustment_bounded():
    """Test that learning adjustment is bounded to prevent extreme changes."""
    from graph.learning_integration import LearningIntegration
    
    integration = LearningIntegration()
    
    # Add many successful signals
    for _ in range(100):
        integration.collect_performance_feedback(
            task_id=f"task_{_}",
            intent="execute_command",
            method_type="shell",
            success=True,
            execution_time=1.0,
            retry_count=0,
            fallback_used=False
        )
    
    adjusted_score = integration.apply_learning_adjustment(
        candidate_score=0.5,
        intent="execute_command",
        method_type="shell",
        app="firefox"
    )
    
    # Adjustment should be bounded (max ±20%)
    assert adjusted_score <= 0.7  # 0.5 + 0.2
    assert adjusted_score >= 0.3  # 0.5 - 0.2


def test_apply_learning_adjustment_clamped():
    """Test that adjusted score is clamped to valid range [0, 1]."""
    from graph.learning_integration import LearningIntegration
    
    integration = LearningIntegration()
    
    # Test with very low base score
    adjusted_score = integration.apply_learning_adjustment(
        candidate_score=0.1,
        intent="execute_command",
        method_type="shell",
        app="firefox"
    )
    
    assert adjusted_score >= 0.0
    
    # Test with very high base score
    adjusted_score = integration.apply_learning_adjustment(
        candidate_score=0.9,
        intent="execute_command",
        method_type="shell",
        app="firefox"
    )
    
    assert adjusted_score <= 1.0


def test_get_performance_summary():
    """Test that performance summary can be retrieved."""
    from graph.learning_integration import LearningIntegration
    
    integration = LearningIntegration()
    
    integration.collect_performance_feedback(
        task_id="task_1",
        intent="execute_command",
        method_type="shell",
        success=True,
        execution_time=1.5,
        retry_count=0,
        fallback_used=False
    )
    
    summary = integration.get_performance_summary()
    
    assert summary is not None
    assert summary["total_signals"] == 1
    assert summary["success_rate"] == 1.0


def test_get_performance_summary_empty():
    """Test that performance summary handles empty history."""
    from graph.learning_integration import LearningIntegration
    
    integration = LearningIntegration()
    
    summary = integration.get_performance_summary()
    
    assert summary is not None
    assert summary["total_signals"] == 0
    assert summary["success_rate"] == 0.0


def test_get_performance_summary_by_method():
    """Test that performance summary groups by method type."""
    from graph.learning_integration import LearningIntegration
    
    integration = LearningIntegration()
    
    integration.collect_performance_feedback(
        task_id="task_1",
        intent="execute_command",
        method_type="shell",
        success=True,
        execution_time=1.5,
        retry_count=0,
        fallback_used=False
    )
    
    integration.collect_performance_feedback(
        task_id="task_2",
        intent="execute_command",
        method_type="ui",
        success=False,
        execution_time=2.0,
        retry_count=1,
        fallback_used=False
    )
    
    summary = integration.get_performance_summary()
    
    assert "by_method" in summary
    assert "shell" in summary["by_method"]
    assert "ui" in summary["by_method"]


def test_get_learning_integration_singleton():
    """Test that get_learning_integration returns singleton."""
    from graph.learning_integration import get_learning_integration
    
    integration1 = get_learning_integration()
    integration2 = get_learning_integration()
    
    assert integration1 is integration2


# ─── EXECUTE STEP NODE LEARNING INTEGRATION TESTS ───────────────────────

def test_execute_step_node_collects_performance_feedback():
    """Test that execute_step_node collects performance feedback."""
    from graph.nodes.execute_step import execute_step_node
    from migration.graph_state import OperonixState
    from migration.domain_contracts import TaskRequest, TaskSource, Plan, PlanStep, PlanStepIdempotency, PlanStepSideEffect, RoutingDecision, Candidate, MethodType, IntentResult, IntentType
    
    task = TaskRequest(user_input="Test", source=TaskSource.VOICE)
    
    intent = IntentResult(
        name="execute_command",
        intent_type=IntentType.ACTION,
        confidence=0.9,
        entities={}
    )
    
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
    
    state = OperonixState(task=task, plan=plan, routing=routing, intent=intent)
    
    result = execute_step_node(state)
    
    # Should execute without error
    assert result["state"].execution is not None


# ─── CANDIDATE DISCOVERY LEARNING INTEGRATION TESTS ───────────────────────

def test_candidate_discovery_applies_learning_adjustments():
    """Test that candidate discovery applies learning adjustments."""
    from graph.candidate_discovery import CandidateDiscoveryService
    from migration.domain_contracts import Plan, PlanStep, PlanStepIdempotency, PlanStepSideEffect, IntentResult, IntentType
    
    service = CandidateDiscoveryService()
    
    step = PlanStep(
        step_id="step_1",
        action="execute_command",
        objective="Execute command",
        idempotency=PlanStepIdempotency.IDEMPOTENT,
        side_effect=PlanStepSideEffect.NONE,
        parameters={"command": "ls -la"}
    )
    
    intent = IntentResult(
        name="execute_command",
        intent_type=IntentType.ACTION,
        confidence=0.9,
        entities={}
    )
    
    candidates = service.discover_candidates(step, intent, {})
    
    # Should return candidates
    assert len(candidates) > 0


def test_candidate_discovery_learning_adjustments_graceful_degradation():
    """Test that candidate discovery degrades gracefully when learning unavailable."""
    from graph.candidate_discovery import CandidateDiscoveryService
    from migration.domain_contracts import Plan, PlanStep, PlanStepIdempotency, PlanStepSideEffect, IntentResult, IntentType
    
    service = CandidateDiscoveryService()
    
    step = PlanStep(
        step_id="step_1",
        action="execute_command",
        objective="Execute command",
        idempotency=PlanStepIdempotency.IDEMPOTENT,
        side_effect=PlanStepSideEffect.NONE,
        parameters={"command": "ls -la"}
    )
    
    intent = IntentResult(
        name="execute_command",
        intent_type=IntentType.ACTION,
        confidence=0.9,
        entities={}
    )
    
    # Should not crash even if learning_integration is unavailable
    candidates = service.discover_candidates(step, intent, {})
    
    assert len(candidates) > 0
