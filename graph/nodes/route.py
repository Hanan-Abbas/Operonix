"""
Route Node — Operonix Graph
────────────────────────────

Route node: Routing engine for execution method selection.
Per migration plan §4.2, node 7:
"route — candidate discovery, evaluation, ranking. Replaces tools/method_router.py
with candidate-based routing engine."
"""
from __future__ import annotations

import logging
from typing import Dict, Any

from migration.graph_state import OperonixState
from migration.domain_contracts import MethodDecision, RoutingCandidate

logger = logging.getLogger("Graph.Route")


def route_node(state: OperonixState) -> Dict[str, Any]:
    """Route node: Select execution method for current plan step.
    
    This node:
    - Discovers candidate execution methods
    - Evaluates candidates based on capability fit, context fit, availability
    - Ranks candidates and selects best method
    - Creates MethodDecision with routing information
    
    In Phase 4, this is a stub that creates a placeholder MethodDecision.
    Later phases will integrate candidate-based routing engine.
    
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
    
    # In Phase 4, we create a placeholder routing decision
    # Later phases will integrate with:
    # - from tools.method_router import MethodRouter
    # - from tools.routing_decision import RoutingDecision
    # - Candidate discovery from capabilities, plugins, tools
    
    logger.info("ROUTE: Candidate-based routing engine deferred to later phases")
    
    # Create placeholder routing candidate
    candidate = RoutingCandidate(
        method_type="SHELL",
        tool_id=None,
        capability_id="execute_intent",
        plugin_id=None,
        capability_fit=0.8,
        context_fit=0.7,
        availability=1.0,
        reliability=0.9,
        overall_score=0.8
    )
    
    # Create placeholder method decision
    method_decision = MethodDecision(
        selected_candidate=candidate,
        confidence=0.8,
        candidates_considered=[candidate],
        routing_explanation="Placeholder routing (Phase 4 stub)"
    )
    
    state.routing = method_decision
    
    state.add_history_event("route_completed", {
        "task_id": state.task.task_id,
        "selected_method": candidate.method_type,
        "confidence": method_decision.confidence
    })
    
    state.update_timestamp()
    
    return {"state": state}
