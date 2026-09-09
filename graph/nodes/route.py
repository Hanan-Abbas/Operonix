"""
Route Node — Operonix Graph
────────────────────────────

Route node: Routing engine for execution method selection.
Per migration plan §4.2, node 7:
"route — candidate discovery, evaluation, ranking. Replaces tools/method_router.py
with candidate-based routing engine."

Phase 10 enhancement: Integrate actual candidate-based routing engine.
"""
from __future__ import annotations

import logging
from typing import Dict, Any

from migration.graph_state import OperonixState
from migration.domain_contracts import MethodDecision, RoutingCandidate, RoutingDecision
from graph.trace_collector import get_trace_collector
from graph.candidate_discovery import get_candidate_discovery_service
from graph.candidate_evaluation import get_candidate_evaluation_service
from graph.ranking_policy import get_ranking_policy_service

logger = logging.getLogger("Graph.Route")


def route_node(state: OperonixState) -> Dict[str, Any]:
    """Route node: Select execution method for current plan step.
    
    This node:
    - Discovers candidate execution methods
    - Evaluates candidates based on capability fit, context fit, availability
    - Ranks candidates and selects best method
    - Creates MethodDecision with routing information
    
    Phase 10 enhancement: Integrate actual candidate-based routing engine.
    
    Args:
        state: Current OperonixState
        
    Returns:
        Dict with updated state including routing decision
    """
    logger.info(f"ROUTE: Routing execution method for task {state.task.task_id}")
    
    state.add_history_event("route_started", {
        "task_id": state.task.task_id,
        "current_step": state.plan.current_step.step_id if state.plan and state.plan.current_step else None
    })
    
    # Phase 10: Integrate actual candidate-based routing engine
    try:
        # Get services
        discovery_service = get_candidate_discovery_service()
        evaluation_service = get_candidate_evaluation_service()
        ranking_service = get_ranking_policy_service()
        
        # Get current step
        if not state.plan or not state.plan.current_step:
            logger.warning("No plan or current step, using fallback routing")
            method_decision = _create_fallback_decision(state)
        else:
            # Discover candidates
            candidates = discovery_service.discover_candidates(
                plan_step=state.plan.current_step,
                intent=state.intent,
                context=state.context if isinstance(state.context, dict) else None
            )
            
            logger.info(f"Discovered {len(candidates)} candidates")
            
            # Evaluate candidates
            evaluations = evaluation_service.evaluate_candidates(
                candidates=candidates,
                plan_step=state.plan.current_step,
                intent=state.intent,
                context=state.context if isinstance(state.context, dict) else None
            )
            
            logger.info(f"Evaluated {len(evaluations)} candidates")
            
            # Rank candidates
            ranked_evaluations = ranking_service.rank_candidates(evaluations)
            
            logger.info(f"Ranked {len(ranked_evaluations)} candidates")
            
            # Make routing decision
            routing_decision = ranking_service.make_routing_decision(ranked_evaluations)
            
            if routing_decision:
                # Convert RoutingDecision to MethodDecision for compatibility
                method_decision = _convert_to_method_decision(routing_decision)
                logger.info(f"Routing decision: {method_decision.selected_candidate.method_type} (confidence: {method_decision.confidence})")
            else:
                logger.warning("No valid routing decision, using fallback")
                method_decision = _create_fallback_decision(state)
        
    except Exception as e:
        logger.error(f"Error in candidate-based routing: {e}, using fallback")
        method_decision = _create_fallback_decision(state)
    
    state.routing = method_decision
    
    # Phase 9: Collect trace events for routing candidates and decision
    trace_collector = get_trace_collector()
    trace_collector.collect_routing_candidates(
        task_id=state.task.task_id,
        candidates=[
            {
                "method_type": c.method_type,
                "tool_id": c.tool_id,
                "capability_fit": c.capability_fit,
                "context_fit": c.context_fit,
                "availability": c.availability,
                "reliability": c.reliability,
                "overall_score": c.overall_score
            }
            for c in method_decision.candidates_considered
        ]
    )
    trace_collector.collect_routing_decision(
        task_id=state.task.task_id,
        decision={
            "selected_method": method_decision.selected_candidate.method_type,
            "confidence": method_decision.confidence,
            "explanation": method_decision.routing_explanation
        }
    )
    
    state.add_history_event("route_completed", {
        "task_id": state.task.task_id,
        "selected_method": method_decision.selected_candidate.method_type,
        "confidence": method_decision.confidence
    })
    
    state.update_timestamp()
    
    return {"state": state}


def _convert_to_method_decision(routing_decision: RoutingDecision) -> MethodDecision:
    """Convert RoutingDecision to MethodDecision for compatibility.
    
    Args:
        routing_decision: RoutingDecision from candidate-based routing
        
    Returns:
        MethodDecision for compatibility with existing code
    """
    # Convert Candidate to RoutingCandidate
    selected_routing_candidate = RoutingCandidate(
        method_type=routing_decision.selected_candidate.candidate_type.value.upper(),
        tool_id=routing_decision.selected_candidate.tool_id,
        capability_id=routing_decision.selected_candidate.capability_id,
        plugin_id=routing_decision.selected_candidate.plugin_id,
        capability_fit=routing_decision.selected_candidate.capability_fit,
        context_fit=routing_decision.selected_candidate.context_fit,
        availability=routing_decision.selected_candidate.availability,
        reliability=routing_decision.selected_candidate.reliability,
        overall_score=routing_decision.selected_candidate.overall_score
    )
    
    # Convert all candidates
    candidates_considered = [
        RoutingCandidate(
            method_type=c.candidate_type.value.upper(),
            tool_id=c.tool_id,
            capability_id=c.capability_id,
            plugin_id=c.plugin_id,
            capability_fit=c.capability_fit,
            context_fit=c.context_fit,
            availability=c.availability,
            reliability=c.reliability,
            overall_score=c.overall_score
        )
        for c in routing_decision.candidates_considered
    ]
    
    return MethodDecision(
        selected_candidate=selected_routing_candidate,
        confidence=routing_decision.confidence,
        candidates_considered=candidates_considered,
        routing_explanation=routing_decision.routing_explanation
    )


def _create_fallback_decision(state: OperonixState) -> MethodDecision:
    """Create fallback routing decision.
    
    Args:
        state: Current OperonixState
        
    Returns:
        Fallback MethodDecision
    """
    candidate = RoutingCandidate(
        method_type="SHELL",
        tool_id="shell",
        capability_id="execute_intent",
        plugin_id=None,
        capability_fit=0.8,
        context_fit=0.7,
        availability=1.0,
        reliability=0.9,
        overall_score=0.8
    )
    
    return MethodDecision(
        selected_candidate=candidate,
        confidence=0.8,
        candidates_considered=[candidate],
        routing_explanation="Fallback routing (candidate-based routing failed or unavailable)"
    )
