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
    """Determine if request is simple or complex using LangChain.
    
    In Phase 3 real implementation, we use LangChain for sophisticated complexity detection.
    Falls back to simple heuristic if LangChain unavailable.
    
    Args:
        user_input: The user's natural language request
        
    Returns:
        True if complex, False if simple
    """
    from migration.feature_flags import flags
    
    # Use LangChain for complexity detection if enabled
    if flags.USE_LANGCHAIN_MODELS:
        try:
            from ai.models.model_service import model_service
            import asyncio
            
            if model_service.is_available():
                return asyncio.run(_detect_complexity_with_langchain(user_input))
        except Exception as e:
            logger.warning(f"LangChain complexity detection failed: {e}, falling back to heuristic")
    
    # Fallback to simple heuristic
    complex_keywords = ["and", "then", "after", "before", "while", "search", "navigate", "multiple", "sequence"]
    
    if len(user_input) > 50:
        return True
    
    if any(keyword in user_input.lower() for keyword in complex_keywords):
        return True
    
    return False


async def _detect_complexity_with_langchain(user_input: str) -> bool:
    """Use LangChain to detect request complexity.
    
    Args:
        user_input: The user's natural language request
        
    Returns:
        True if complex, False if simple
    """
    from ai.models.model_service import model_service
    
    messages = [
        {
            "role": "system",
            "content": """You are a complexity analyzer for an AI agent. Determine if the user's request is simple or complex.

Simple requests:
- Single action (e.g., "Open Firefox", "Create file")
- No conditional logic
- No sequencing

Complex requests:
- Multiple actions (e.g., "Open Firefox and search for agents")
- Conditional logic (e.g., "if file exists, delete it")
- Sequencing (e.g., "then", "after", "before")
- Multi-step workflows

Respond in JSON format with key: is_complex (true/false)."""
        },
        {
            "role": "user",
            "content": user_input
        }
    ]
    
    schema = {
        "name": "complexity_analysis",
        "properties": {
            "is_complex": {"type": "boolean"}
        }
    }
    
    result = await model_service.generate_structured_output(messages, schema)
    return result.get("is_complex", False)


def _generate_simple_plan(state: OperonixState) -> Plan:
    """Generate a deterministic plan for simple requests.
    
    Simple plans are deterministic and don't require AI.
    They typically involve a single action.
    
    This integrates with brain/planner.py for static step generation.
    
    Args:
        state: Current OperonixState
        
    Returns:
        Plan with deterministic steps
    """
    import uuid
    
    try:
        from brain.planner import Planner
        
        # Try to use existing Planner's static step generation
        planner = Planner()
        
        # Resolve args using existing Planner logic
        resolved_args = planner._resolve_args_for_intent(
            state.intent.name if state.intent else "unknown",
            state.intent.parameters if state.intent else {},
            state.context if hasattr(state, 'context') else {},
            {"task_id": state.task.task_id, "user_input": state.task.user_input}
        )
        
        # Generate static steps using existing Planner logic
        # This is a simplified integration - full integration would need async context
        # For now, we create a single step with resolved args
        step = PlanStep(
            step_id=str(uuid.uuid4()),
            action="execute_intent",
            arguments={
                "intent": state.intent.name if state.intent else "unknown",
                "parameters": resolved_args
            },
            objective=f"Execute intent: {state.task.user_input}",
            idempotency="CONDITIONAL",
            side_effect="LIMITED_SIDE_EFFECT",
            reversibility=True
        )
        
        logger.info(f"Generated simple plan using brain/planner.py integration")
        return Plan(steps=[step])
        
    except ImportError:
        logger.warning("Could not import brain/planner.py, using fallback simple plan")
        # Fallback to simple plan without Planner integration
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
    
    In Phase 3 real implementation, this uses LangChain for actual plan generation.
    Falls back to placeholder if LangChain unavailable.
    
    Args:
        state: Current OperonixState
        
    Returns:
        Plan with AI-generated steps
    """
    from migration.feature_flags import flags
    
    # Use LangChain for plan generation if enabled
    if flags.USE_LANGCHAIN_MODELS:
        try:
            import asyncio
            plan = asyncio.run(_generate_plan_with_langchain(state))
            if plan:
                return plan
        except Exception as e:
            logger.warning(f"LangChain plan generation failed: {e}, falling back to placeholder")
    
    # Fallback to placeholder multi-step plan
    logger.info("Using placeholder complex plan (LangChain unavailable)")
    return _generate_placeholder_complex_plan(state)


async def _generate_plan_with_langchain(state: OperonixState) -> Plan:
    """Use LangChain to generate a plan for complex requests.
    
    Args:
        state: Current OperonixState
        
    Returns:
        Plan with AI-generated steps
    """
    from ai.models.model_service import model_service
    import uuid
    
    messages = [
        {
            "role": "system",
            "content": """You are a planning agent for an AI assistant. Break down the user's request into a sequence of executable steps.

Each step should have:
- action: The type of action (e.g., "prepare_environment", "execute_intent", "verify_result")
- objective: A clear description of what the step achieves
- arguments: Key parameters needed for the step

Respond in JSON format with key: steps (array of step objects)."""
        },
        {
            "role": "user",
            "content": f"User request: {state.task.user_input}\nIntent: {state.intent.name if state.intent else 'unknown'}\nParameters: {state.intent.parameters if state.intent else {}}"
        }
    ]
    
    schema = {
        "name": "plan_generation",
        "properties": {
            "steps": {
                "type": "array",
                "items": {
                    "type": "object",
                    "properties": {
                        "action": {"type": "string"},
                        "objective": {"type": "string"},
                        "arguments": {"type": "object"}
                    }
                }
            }
        }
    }
    
    result = await model_service.generate_structured_output(messages, schema)
    
    # Convert LangChain response to PlanStep objects
    steps = []
    for step_data in result.get("steps", []):
        step = PlanStep(
            step_id=str(uuid.uuid4()),
            action=step_data.get("action", "unknown"),
            arguments=step_data.get("arguments", {}),
            objective=step_data.get("objective", ""),
            idempotency="CONDITIONAL",
            side_effect="LIMITED_SIDE_EFFECT",
            reversibility=True
        )
        steps.append(step)
    
    # Add dependencies (simple sequential for now)
    for i in range(1, len(steps)):
        steps[i].dependencies = [steps[i-1].step_id]
    
    return Plan(steps=steps)


def _generate_placeholder_complex_plan(state: OperonixState) -> Plan:
    """Generate a placeholder multi-step plan when LangChain is unavailable.
    
    Args:
        state: Current OperonixState
        
    Returns:
        Plan with placeholder steps
    """
    import uuid
    
    # Create a placeholder multi-step plan
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
