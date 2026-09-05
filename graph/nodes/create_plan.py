"""
Create Plan Node — Operonix Graph
────────────────────────────────

Create plan node: Planning integration with deterministic/AI split.
Per migration plan §4.2, node 4:
"create_plan — simple → deterministic plan; complex → LangChain-backed plan.
The graph owns current step, completed steps, workflow position.
The planner owns what the steps are."
"""
from __future__ import annotations

import logging
from typing import Dict, Any

from migration.graph_state import OperonixState
from migration.domain_contracts import Plan, PlanStep

logger = logging.getLogger("Graph.CreatePlan")


def create_plan_node(state: OperonixState) -> Dict[str, Any]:
    """Create plan node: Generate execution plan with deterministic/AI split.
    
    This node:
    - Determines if request is simple or complex
    - Simple requests → deterministic plan
    - Complex requests → LangChain-backed plan
    - Creates Plan with PlanStep objects
    - Adds idempotency and side-effect classification to steps
    
    Per migration plan §4.2:
    "The graph owns current step, completed steps, workflow position.
    The planner owns what the steps are."
    
    In Phase 3, this is a stub that creates a placeholder plan.
    Later phases will integrate with existing brain/planner.py.
    
    Args:
        state: Current OperonixState
        
    Returns:
        Dict with updated state including plan
    """
    logger.info(f"CREATE_PLAN: Generating plan for task {state.task.task_id}")
    
    state.add_history_event("create_plan_started", {
        "task_id": state.task.task_id,
        "intent": state.intent.name if state.intent else None
    })
    
    # Determine if request is simple or complex
    # In Phase 3, we use a simple heuristic
    # Later phases will use more sophisticated detection
    is_complex = _is_complex_request(state.task.user_input)
    
    logger.info(f"CREATE_PLAN: Request classified as {'complex' if is_complex else 'simple'}")
    
    # Generate plan based on complexity
    if is_complex:
        plan = _generate_complex_plan(state)
    else:
        plan = _generate_simple_plan(state)
    
    state.plan = plan
    
    state.add_history_event("create_plan_completed", {
        "task_id": state.task.task_id,
        "plan_id": plan.plan_id,
        "num_steps": len(plan.steps),
        "complexity": "complex" if is_complex else "simple"
    })
    
    state.update_timestamp()
    
    return {"state": state}


def _is_complex_request(user_input: str) -> bool:
    """Determine if request is simple or complex.
    
    In Phase 3, we use a simple heuristic based on input length and keywords.
    Later phases will use more sophisticated detection (e.g., LangChain classification).
    
    Args:
        user_input: The user's natural language request
        
    Returns:
        True if complex, False if simple
    """
    # Simple heuristic: complex if input is long or contains certain keywords
    complex_keywords = ["and", "then", "after", "before", "while", "search", "navigate"]
    
    if len(user_input) > 50:
        return True
    
    if any(keyword in user_input.lower() for keyword in complex_keywords):
        return True
    
    return False


def _generate_simple_plan(state: OperonixState) -> Plan:
    """Generate a deterministic plan for simple requests.
    
    Simple plans are deterministic and don't require AI.
    They typically involve a single action.
    
    Args:
        state: Current OperonixState
        
    Returns:
        Plan with deterministic steps
    """
    import uuid
    
    # Create a single-step plan
    step = PlanStep(
        step_id=str(uuid.uuid4()),
        action="execute_intent",
        arguments={
            "intent": state.intent.name if state.intent else "unknown",
            "parameters": state.intent.parameters if state.intent else {}
        },
        objective=f"Execute intent: {state.task.user_input}",
        idempotency="CONDITIONAL",
        side_effect="LIMITED_SIDE_EFFECT",
        reversibility=True
    )
    
    return Plan(steps=[step])


def _generate_complex_plan(state: OperonixState) -> Plan:
    """Generate a LangChain-backed plan for complex requests.
    
    Complex plans require AI reasoning to determine the sequence of steps.
    They typically involve multiple actions or conditional logic.
    
    In Phase 3, this is a stub that creates a placeholder multi-step plan.
    Later phases will integrate with LangChain for actual plan generation.
    
    Args:
        state: Current OperonixState
        
    Returns:
        Plan with AI-generated steps
    """
    import uuid
    
    logger.info("CREATE_PLAN: LangChain-backed plan generation deferred to later phases")
    
    # Create a placeholder multi-step plan
    # Later phases will use LangChain to generate actual steps
    step1 = PlanStep(
        step_id=str(uuid.uuid4()),
        action="prepare_environment",
        arguments={},
        objective="Prepare environment for execution",
        idempotency="SAFE",
        side_effect="READ_ONLY",
        reversibility=True
    )
    
    step2 = PlanStep(
        step_id=str(uuid.uuid4()),
        action="execute_intent",
        arguments={
            "intent": state.intent.name if state.intent else "unknown",
            "parameters": state.intent.parameters if state.intent else {}
        },
        objective=f"Execute intent: {state.task.user_input}",
        idempotency="CONDITIONAL",
        side_effect="LIMITED_SIDE_EFFECT",
        reversibility=True,
        dependencies=[step1.step_id]
    )
    
    return Plan(steps=[step1, step2])
