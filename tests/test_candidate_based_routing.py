"""
Candidate-Based Routing Tests — Operonix Migration Phase 10
────────────────────────────────────────────────────────────

Tests for candidate-based routing engine.
Per migration plan Phase 10: Candidate-Based Routing Engine
"""
from __future__ import annotations

import pytest


# ─── CANDIDATE TYPE TESTS ─────────────────────────────────────────────────────

def test_candidate_type_enum():
    """Test that CandidateType enum has all required values."""
    from migration.domain_contracts import CandidateType
    
    assert CandidateType.PLUGIN.value == "plugin"
    assert CandidateType.API.value == "api"
    assert CandidateType.SHELL.value == "shell"
    assert CandidateType.UI.value == "ui"
    assert CandidateType.BROWSER_AUTOMATION.value == "browser_automation"
    assert CandidateType.VISION.value == "vision"
    assert CandidateType.REMOTE.value == "remote"
    assert CandidateType.LOCAL.value == "local"


# ─── CANDIDATE TESTS ───────────────────────────────────────────────────────

def test_candidate_domain_object():
    """Test that Candidate is a valid domain object."""
    from migration.domain_contracts import Candidate, CandidateType
    
    candidate = Candidate(
        candidate_type=CandidateType.SHELL,
        tool_id="shell",
        capability_id="execute_command"
    )
    
    assert candidate.candidate_type == CandidateType.SHELL
    assert candidate.tool_id == "shell"
    assert candidate.candidate_id is not None
    assert candidate.overall_score == 0.0


def test_candidate_with_evaluation_metrics():
    """Test that Candidate can have evaluation metrics."""
    from migration.domain_contracts import Candidate, CandidateType
    
    candidate = Candidate(
        candidate_type=CandidateType.SHELL,
        tool_id="shell",
        capability_id="execute_command",
        capability_fit=0.9,
        context_fit=0.8,
        availability=1.0,
        reliability=0.9,
        historical_success=0.8,
        risk=0.3,
        permissions=0.9,
        latency=0.9,
        reversibility=0.7
    )
    
    assert candidate.capability_fit == 0.9
    assert candidate.context_fit == 0.8
    assert candidate.availability == 1.0


def test_candidate_overall_score_calculation():
    """Test that overall score is calculated correctly."""
    from migration.domain_contracts import Candidate, CandidateType
    
    candidate = Candidate(
        candidate_type=CandidateType.SHELL,
        tool_id="shell",
        capability_id="execute_command",
        capability_fit=0.9,
        context_fit=0.8,
        availability=1.0,
        reliability=0.9,
        historical_success=0.8,
        risk=0.3,
        permissions=0.9,
        latency=0.9,
        reversibility=0.7
    )
    
    # Calculate expected score using default weights
    expected_score = (
        0.9 * 0.25 +  # capability_fit
        0.8 * 0.20 +  # context_fit
        1.0 * 0.15 +  # availability
        0.9 * 0.15 +  # reliability
        0.8 * 0.10 +  # historical_success
        0.3 * 0.05 +  # risk
        0.9 * 0.05 +  # permissions
        0.9 * 0.03 +  # latency
        0.7 * 0.02    # reversibility
    )
    
    assert abs(candidate.overall_score - expected_score) < 0.01


# ─── CANDIDATE EVALUATION TESTS ───────────────────────────────────────────────

def test_candidate_evaluation_domain_object():
    """Test that CandidateEvaluation is a valid domain object."""
    from migration.domain_contracts import Candidate, CandidateEvaluation, CandidateType
    
    candidate = Candidate(
        candidate_type=CandidateType.SHELL,
        tool_id="shell",
        capability_id="execute_command"
    )
    
    evaluation = CandidateEvaluation(
        candidate=candidate,
        evaluation_reason="Good fit"
    )
    
    assert evaluation.candidate == candidate
    assert evaluation.evaluation_reason == "Good fit"
    assert evaluation.constraints_satisfied is True


def test_candidate_evaluation_with_constraint_violations():
    """Test that CandidateEvaluation can have constraint violations."""
    from migration.domain_contracts import Candidate, CandidateEvaluation, CandidateType
    
    candidate = Candidate(
        candidate_type=CandidateType.SHELL,
        tool_id="shell",
        capability_id="execute_command",
        availability=0.3
    )
    
    evaluation = CandidateEvaluation(
        candidate=candidate,
        evaluation_reason="Low availability",
        constraints_satisfied=False,
        constraint_violations=["Low availability"]
    )
    
    assert evaluation.constraints_satisfied is False
    assert len(evaluation.constraint_violations) == 1


# ─── RANKING POLICY TESTS ───────────────────────────────────────────────────

def test_ranking_policy_domain_object():
    """Test that RankingPolicy is a valid domain object."""
    from migration.domain_contracts import RankingPolicy
    
    policy = RankingPolicy(
        policy_name="default"
    )
    
    assert policy.policy_name == "default"
    assert policy.policy_id is not None
    assert policy.min_threshold == 0.5
    assert policy.require_all_constraints is True


def test_ranking_policy_custom_weights():
    """Test that RankingPolicy can have custom weights."""
    from migration.domain_contracts import RankingPolicy
    
    policy = RankingPolicy(
        policy_name="custom",
        weights={
            "capability_fit": 0.5,
            "context_fit": 0.3,
            "availability": 0.2
        },
        min_threshold=0.7
    )
    
    assert policy.weights["capability_fit"] == 0.5
    assert policy.weights["context_fit"] == 0.3
    assert policy.min_threshold == 0.7


# ─── ROUTING DECISION TESTS ─────────────────────────────────────────────────

def test_routing_decision_domain_object():
    """Test that RoutingDecision is a valid domain object."""
    from migration.domain_contracts import RoutingDecision, Candidate, CandidateType
    
    candidate = Candidate(
        candidate_type=CandidateType.SHELL,
        tool_id="shell",
        capability_id="execute_command",
        overall_score=0.9
    )
    
    decision = RoutingDecision(
        selected_candidate=candidate,
        confidence=0.9,
        routing_explanation="Best candidate"
    )
    
    assert decision.selected_candidate == candidate
    assert decision.confidence == 0.9
    assert decision.routing_explanation == "Best candidate"


def test_routing_decision_with_candidates_considered():
    """Test that RoutingDecision can include all candidates considered."""
    from migration.domain_contracts import RoutingDecision, Candidate, CandidateType
    
    candidate1 = Candidate(
        candidate_type=CandidateType.SHELL,
        tool_id="shell",
        capability_id="execute_command",
        overall_score=0.9
    )
    
    candidate2 = Candidate(
        candidate_type=CandidateType.API,
        tool_id="api",
        capability_id="api_execute",
        overall_score=0.7
    )
    
    decision = RoutingDecision(
        selected_candidate=candidate1,
        candidates_considered=[candidate1, candidate2],
        confidence=0.9,
        routing_explanation="Best candidate"
    )
    
    assert len(decision.candidates_considered) == 2


# ─── CANDIDATE DISCOVERY SERVICE TESTS ─────────────────────────────────────

def test_candidate_discovery_service_init():
    """Test that CandidateDiscoveryService can be initialized."""
    from graph.candidate_discovery import CandidateDiscoveryService
    
    service = CandidateDiscoveryService()
    assert service is not None
    assert len(service.available_plugins) == 0
    assert len(service.available_apis) == 0
    assert len(service.available_tools) == 0


def test_candidate_discovery_register_plugin():
    """Test that plugins can be registered."""
    from graph.candidate_discovery import CandidateDiscoveryService
    
    service = CandidateDiscoveryService()
    service.register_plugin("test_plugin", {"capabilities": ["execute"]})
    
    assert "test_plugin" in service.available_plugins


def test_candidate_discovery_register_api():
    """Test that APIs can be registered."""
    from graph.candidate_discovery import CandidateDiscoveryService
    
    service = CandidateDiscoveryService()
    service.register_api("test_api", {"endpoints": ["/execute"]})
    
    assert "test_api" in service.available_apis


def test_candidate_discovery_discover_shell_candidates():
    """Test that shell candidates are always discovered."""
    from graph.candidate_discovery import CandidateDiscoveryService
    from migration.domain_contracts import PlanStep, PlanStepIdempotency, PlanStepSideEffect
    from migration.domain_contracts import CandidateType
    
    service = CandidateDiscoveryService()
    
    step = PlanStep(
        step_id="step_1",
        objective="Execute command",
        idempotency=PlanStepIdempotency.IDEMPOTENT,
        side_effect=PlanStepSideEffect.NONE
    )
    
    candidates = service.discover_candidates(step, None, None)
    
    shell_candidates = [c for c in candidates if c.candidate_type == CandidateType.SHELL]
    assert len(shell_candidates) >= 1


def test_candidate_discovery_discover_api_candidates():
    """Test that API candidates are discovered when registered."""
    from graph.candidate_discovery import CandidateDiscoveryService
    from migration.domain_contracts import PlanStep, PlanStepIdempotency, PlanStepSideEffect, CandidateType
    
    service = CandidateDiscoveryService()
    service.register_api("test_api", {"capabilities": ["execute"], "capability_id": "api_execute"})
    
    step = PlanStep(
        step_id="step_1",
        objective="Execute via API",
        idempotency=PlanStepIdempotency.IDEMPOTENT,
        side_effect=PlanStepSideEffect.NONE
    )
    
    candidates = service.discover_candidates(step, None, None)
    
    api_candidates = [c for c in candidates if c.candidate_type == CandidateType.API]
    assert len(api_candidates) >= 1


def test_candidate_discovery_discover_ui_candidates():
    """Test that UI candidates are discovered when context has window."""
    from graph.candidate_discovery import CandidateDiscoveryService
    from migration.domain_contracts import PlanStep, PlanStepIdempotency, PlanStepSideEffect, CandidateType
    
    service = CandidateDiscoveryService()
    
    step = PlanStep(
        step_id="step_1",
        objective="Click button",
        idempotency=PlanStepIdempotency.IDEMPOTENT,
        side_effect=PlanStepSideEffect.NONE
    )
    
    context = {"window_title": "Test Window"}
    
    candidates = service.discover_candidates(step, None, context)
    
    ui_candidates = [c for c in candidates if c.candidate_type == CandidateType.UI]
    assert len(ui_candidates) >= 1


# ─── CANDIDATE EVALUATION SERVICE TESTS ───────────────────────────────────

def test_candidate_evaluation_service_init():
    """Test that CandidateEvaluationService can be initialized."""
    from graph.candidate_evaluation import CandidateEvaluationService
    
    service = CandidateEvaluationService()
    assert service is not None
    assert len(service.historical_data) == 0


def test_candidate_evaluation_evaluate_candidates():
    """Test that candidates can be evaluated."""
    from graph.candidate_evaluation import CandidateEvaluationService
    from migration.domain_contracts import Candidate, CandidateType, PlanStep, PlanStepIdempotency, PlanStepSideEffect
    
    service = CandidateEvaluationService()
    
    candidate = Candidate(
        candidate_type=CandidateType.SHELL,
        tool_id="shell",
        capability_id="execute_command"
    )
    
    step = PlanStep(
        step_id="step_1",
        objective="Execute command",
        idempotency=PlanStepIdempotency.IDEMPOTENT,
        side_effect=PlanStepSideEffect.NONE
    )
    
    evaluations = service.evaluate_candidates([candidate], step, None, None)
    
    assert len(evaluations) == 1
    assert evaluations[0].candidate == candidate
    assert evaluations[0].candidate.overall_score > 0


def test_candidate_evaluation_capability_fit():
    """Test that capability fit is evaluated correctly."""
    from graph.candidate_evaluation import CandidateEvaluationService
    from migration.domain_contracts import Candidate, CandidateType, PlanStep, PlanStepIdempotency, PlanStepSideEffect
    
    service = CandidateEvaluationService()
    
    candidate = Candidate(
        candidate_type=CandidateType.SHELL,
        tool_id="shell",
        capability_id="execute_command"
    )
    
    step = PlanStep(
        step_id="step_1",
        objective="Execute command",
        idempotency=PlanStepIdempotency.IDEMPOTENT,
        side_effect=PlanStepSideEffect.NONE
    )
    
    evaluations = service.evaluate_candidates([candidate], step, None, None)
    
    assert evaluations[0].candidate.capability_fit > 0.8  # Shell should have high capability fit for command execution


def test_candidate_evaluation_context_fit():
    """Test that context fit is evaluated correctly."""
    from graph.candidate_evaluation import CandidateEvaluationService
    from migration.domain_contracts import Candidate, CandidateType, PlanStep, PlanStepIdempotency, PlanStepSideEffect
    
    service = CandidateEvaluationService()
    
    candidate = Candidate(
        candidate_type=CandidateType.UI,
        tool_id="ui_automation",
        capability_id="ui_interact"
    )
    
    step = PlanStep(
        step_id="step_1",
        objective="Click button",
        idempotency=PlanStepIdempotency.IDEMPOTENT,
        side_effect=PlanStepSideEffect.NONE
    )
    
    context = {"window_title": "Test Window"}
    
    evaluations = service.evaluate_candidates([candidate], step, None, context)
    
    assert evaluations[0].candidate.context_fit > 0.8  # UI should have high context fit with window


def test_candidate_evaluation_constraints():
    """Test that constraints are checked correctly."""
    from graph.candidate_evaluation import CandidateEvaluationService
    from migration.domain_contracts import Candidate, CandidateType, PlanStep, PlanStepIdempotency, PlanStepSideEffect
    
    service = CandidateEvaluationService()
    
    candidate = Candidate(
        candidate_type=CandidateType.SHELL,
        tool_id="shell",
        capability_id="execute_command",
        availability=0.3  # Low availability
    )
    
    step = PlanStep(
        step_id="step_1",
        objective="Execute command",
        idempotency=PlanStepIdempotency.IDEMPOTENT,
        side_effect=PlanStepSideEffect.NONE
    )
    
    evaluations = service.evaluate_candidates([candidate], step, None, None)
    
    assert evaluations[0].constraints_satisfied is False
    assert len(evaluations[0].constraint_violations) > 0


def test_candidate_evaluation_update_historical_data():
    """Test that historical data can be updated."""
    from graph.candidate_evaluation import CandidateEvaluationService
    from migration.domain_contracts import Candidate, CandidateType
    
    service = CandidateEvaluationService()
    
    candidate = Candidate(
        candidate_type=CandidateType.SHELL,
        tool_id="shell",
        capability_id="execute_command"
    )
    
    service.update_historical_data(candidate, success=True)
    
    candidate_key = f"{candidate.candidate_type.value}_{candidate.tool_id}"
    assert candidate_key in service.historical_data
    assert service.historical_data[candidate_key]["success_rate"] == 1.0


# ─── RANKING POLICY SERVICE TESTS ─────────────────────────────────────────

def test_ranking_policy_service_init():
    """Test that RankingPolicyService can be initialized."""
    from graph.ranking_policy import RankingPolicyService
    
    service = RankingPolicyService()
    assert service is not None
    assert service.default_policy is not None


def test_ranking_policy_rank_candidates():
    """Test that candidates can be ranked."""
    from graph.ranking_policy import RankingPolicyService
    from migration.domain_contracts import Candidate, CandidateEvaluation, CandidateType
    
    service = RankingPolicyService()
    
    candidate1 = Candidate(
        candidate_type=CandidateType.SHELL,
        tool_id="shell",
        capability_id="execute_command",
        overall_score=0.7
    )
    
    candidate2 = Candidate(
        candidate_type=CandidateType.API,
        tool_id="api",
        capability_id="api_execute",
        overall_score=0.9
    )
    
    evaluation1 = CandidateEvaluation(candidate=candidate1)
    evaluation2 = CandidateEvaluation(candidate=candidate2)
    
    ranked = service.rank_candidates([evaluation1, evaluation2])
    
    assert len(ranked) == 2
    assert ranked[0].candidate.overall_score >= ranked[1].candidate.overall_score


def test_ranking_policy_filter_constraints():
    """Test that candidates not satisfying constraints are filtered."""
    from graph.ranking_policy import RankingPolicyService
    from migration.domain_contracts import Candidate, CandidateEvaluation, CandidateType
    
    service = RankingPolicyService()
    
    candidate1 = Candidate(
        candidate_type=CandidateType.SHELL,
        tool_id="shell",
        capability_id="execute_command",
        overall_score=0.9
    )
    
    candidate2 = Candidate(
        candidate_type=CandidateType.API,
        tool_id="api",
        capability_id="api_execute",
        overall_score=0.7,
        availability=0.3
    )
    
    evaluation1 = CandidateEvaluation(candidate=candidate1, constraints_satisfied=True)
    evaluation2 = CandidateEvaluation(candidate=candidate2, constraints_satisfied=False)
    
    ranked = service.rank_candidates([evaluation1, evaluation2])
    
    # Only candidate1 should be in the ranked list
    assert len(ranked) == 1
    assert ranked[0].candidate.tool_id == "shell"


def test_ranking_policy_make_routing_decision():
    """Test that routing decision can be made."""
    from graph.ranking_policy import RankingPolicyService
    from migration.domain_contracts import Candidate, CandidateEvaluation, CandidateType
    
    service = RankingPolicyService()
    
    candidate = Candidate(
        candidate_type=CandidateType.SHELL,
        tool_id="shell",
        capability_id="execute_command",
        overall_score=0.9
    )
    
    evaluation = CandidateEvaluation(candidate=candidate)
    
    ranked = service.rank_candidates([evaluation])
    decision = service.make_routing_decision(ranked)
    
    assert decision is not None
    assert decision.selected_candidate == candidate
    assert decision.confidence == 0.9


def test_ranking_policy_below_threshold():
    """Test that candidates below threshold are rejected."""
    from graph.ranking_policy import RankingPolicyService
    from migration.domain_contracts import Candidate, CandidateEvaluation, CandidateType
    
    service = RankingPolicyService()
    
    candidate = Candidate(
        candidate_type=CandidateType.SHELL,
        tool_id="shell",
        capability_id="execute_command",
        overall_score=0.3  # Below default threshold of 0.5
    )
    
    evaluation = CandidateEvaluation(candidate=candidate)
    
    ranked = service.rank_candidates([evaluation])
    decision = service.make_routing_decision(ranked)
    
    assert decision is None


def test_ranking_policy_create_custom_policy():
    """Test that custom policies can be created."""
    from graph.ranking_policy import RankingPolicyService
    
    service = RankingPolicyService()
    
    custom_policy = service.create_custom_policy(
        policy_name="custom",
        weights={"capability_fit": 0.5, "context_fit": 0.5},
        min_threshold=0.7
    )
    
    assert custom_policy.policy_name == "custom"
    assert custom_policy.weights["capability_fit"] == 0.5
    assert custom_policy.min_threshold == 0.7


# ─── ROUTE NODE INTEGRATION TESTS ───────────────────────────────────────────

def test_route_node_uses_candidate_based_routing():
    """Test that route node uses candidate-based routing."""
    from graph.nodes.route import route_node
    from migration.graph_state import OperonixState
    from migration.domain_contracts import TaskRequest, TaskSource, Plan, PlanStep, PlanStepIdempotency, PlanStepSideEffect
    
    task = TaskRequest(user_input="Execute command", source=TaskSource.VOICE)
    
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
    
    state = OperonixState(task=task, plan=plan)
    
    result = route_node(state)
    
    assert result["state"].routing is not None
    assert result["state"].routing.selected_candidate is not None


def test_route_node_fallback_on_error():
    """Test that route node falls back on error."""
    from graph.nodes.route import route_node
    from migration.graph_state import OperonixState
    from migration.domain_contracts import TaskRequest, TaskSource
    
    task = TaskRequest(user_input="Test", source=TaskSource.VOICE)
    state = OperonixState(task=task)  # No plan
    
    result = route_node(state)
    
    assert result["state"].routing is not None
    assert "fallback" in result["state"].routing.routing_explanation.lower()


def test_convert_to_method_decision():
    """Test that RoutingDecision can be converted to MethodDecision."""
    from graph.nodes.route import _convert_to_method_decision
    from migration.domain_contracts import RoutingDecision, Candidate, CandidateType
    
    candidate = Candidate(
        candidate_type=CandidateType.SHELL,
        tool_id="shell",
        capability_id="execute_command",
        overall_score=0.9
    )
    
    routing_decision = RoutingDecision(
        selected_candidate=candidate,
        confidence=0.9,
        routing_explanation="Best candidate"
    )
    
    method_decision = _convert_to_method_decision(routing_decision)
    
    assert method_decision.selected_candidate.method_type == "SHELL"
    assert method_decision.confidence == 0.9
