"""
Create Plan Node — Operonix Graph
────────────────────────────────

Create plan node: Planning integration with deterministic/AI split.
Per migration plan §4.2, node 4:
"create_plan — simple → deterministic plan; complex → LangChain-backed plan.
The graph owns current step, completed steps, workflow position.
The planner owns what the steps are.
"""
from __future__ import annotations

import logging
import uuid
from typing import Dict, Any

from migration.graph_state import OperonixState
from migration.domain_contracts import Plan, PlanStep, PlanStepIdempotency, PlanStepSideEffect
from graph.trace_collector import get_trace_collector
from graph.async_helpers import run_async_safely
from graph.state_helpers import get_safe_field, get_intent_name, validate_state_for_node
from graph.error_helpers import handle_recoverable_error, track_error

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
    
    # Validate required state fields
    validate_state_for_node(state, "create_plan", ["task", "intent"])
    
    state.add_history_event("create_plan_started", {
        "task_id": state.task.task_id,
        "intent": get_intent_name(state, None)
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
    
    logger.info(f"CREATE_PLAN: Created plan with {len(plan.steps)} steps, current_step_index={plan.current_step_index}")
    if plan.steps:
        logger.info(f"CREATE_PLAN: First step: {plan.steps[0].step_id}, action={plan.steps[0].action}")
    
    # Phase 9: Collect trace event for plan
    trace_collector = get_trace_collector()
    trace_collector.collect_plan(
        task_id=state.task.task_id,
        plan_data={
            "plan_id": plan.plan_id,
            "num_steps": len(plan.steps),
            "complexity": "complex" if is_complex else "simple",
            "steps": [
                {
                    "step_id": step.step_id,
                    "objective": step.objective,
                    "idempotency": step.idempotency,
                    "side_effect": step.side_effect
                }
                for step in plan.steps
            ]
        }
    )
    
    state.add_history_event("create_plan_completed", {
        "task_id": state.task.task_id,
        "plan_id": plan.plan_id,
        "num_steps": len(plan.steps),
        "complexity": "complex" if is_complex else "simple"
    })
    
    state.update_timestamp()
    
    return {"plan": plan}


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
            
            if model_service.is_available():
                # Use run_async_safely to call async LangChain from sync context
                is_complex = run_async_safely(
                    _detect_complexity_with_langchain,
                    user_input,
                    timeout=30.0
                )
                return is_complex
        except Exception as e:
            # LangChain complexity detection is optional, treat as recoverable
            graph_error = handle_recoverable_error(
                e,
                "create_plan",
                fallback_description="falling back to heuristic complexity detection"
            )
            # Note: We don't track this error in state as it's a fallback path
    
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


async def _generate_command_with_llm(
    user_input: str,
    intent_name: str,
    intent_parameters: Dict[str, Any],
    context: Optional[Dict[str, Any]] = None
) -> Dict[str, Any]:
    """Use LangChain to generate concrete execution command.
    
    This function uses the LLM to translate natural language into concrete
    shell commands, API calls, plugin invocations, or UI automation commands.
    This removes the need for hardcoded command translators and allows
    the system to handle any scenario without domain restrictions.
    
    Args:
        user_input: The user's natural language request
        intent_name: The parsed intent name
        intent_parameters: Parameters extracted from intent
        context: Current context information
        
    Returns:
        Dictionary with:
        - command: the concrete command to execute
        - arguments: list of command-line arguments
        - method: "shell", "api", "plugin", or "ui"
        - explanation: brief explanation of why this command
    """
    from ai.models.model_service import model_service
    
    # Build context information for the LLM
    context_info = ""
    if context:
        app_name = context.get("app_name", "unknown")
        app_type = context.get("app_type", "unknown")
        cwd = context.get("cwd", "unknown")
        context_info = f"\nCurrent context: app={app_name}, type={app_type}, cwd={cwd}"
    
    messages = [
        {
            "role": "system",
            "content": """You are a command generator for an AI agent.
Given a user request, generate the concrete execution command.

CRITICAL: Separate the base command from its arguments correctly:
- command: ONLY the base executable name (e.g., "ping", "gnome-system-monitor", "firefox")
- arguments: ONLY the flags/parameters (e.g., ["-c", "4", "www.example.com"])

Examples:
- "ping google.com 4 times" → command: "ping", arguments: ["-c", "4", "google.com"]
- "open system monitor" → command: "gnome-system-monitor", arguments: []
- "create file test.txt" → command: "touch", arguments: ["test.txt"]
- "list files" → command: "ls", arguments: []

Rules:
- For opening applications: use the actual system command (e.g., "gnome-system-monitor", "firefox", "google-chrome", "gnome-terminal")
- For file operations: use standard shell commands (touch, mkdir, rm, cp, mv, cat, echo)
- For web operations: use browser commands or specify API calls
- For searching: use appropriate search tools (grep, find, or browser commands)
- For installation: use package managers (apt, pip, npm, etc.)
- For complex tasks: break into the primary concrete command

Platform-specific guidance:
- Linux (GNOME/KDE): Use system application names (gnome-system-monitor, nautilus, gedit, etc.)
- macOS: Use open -a for applications
- Windows: Use start or full paths

Respond in JSON with:
- command: the base executable name ONLY (string, no arguments)
- arguments: list of command-line arguments (array of strings)
- method: "shell", "api", "plugin", or "ui" (string)
- explanation: brief explanation of why this command (string)"""
        },
        {
            "role": "user",
            "content": f"User request: {user_input}\nIntent: {intent_name}\nParameters: {intent_parameters}{context_info}"
        }
    ]
    
    schema = {
        "name": "command_generation",
        "properties": {
            "command": {"type": "string"},
            "arguments": {"type": "array", "items": {"type": "string"}},
            "method": {"type": "string", "enum": ["shell", "api", "plugin", "ui"]},
            "explanation": {"type": "string"}
        }
    }
    
    result = await model_service.generate_structured_output(messages, schema)
    
    # Validate and sanitize the result
    command = result.get("command", "").strip()
    arguments = result.get("arguments", [])
    method = result.get("method", "shell")
    explanation = result.get("explanation", "")

    # Ensure arguments is a list of strings
    if not isinstance(arguments, list):
        arguments = []
    arguments = [str(arg) for arg in arguments]

    # Fallback: if LLM returned command with arguments combined, split intelligently
    # Only split if it looks like a common pattern (e.g., "ping -c 4 google.com")
    if not arguments and " " in command:
        # Smart splitting: keep flags and their values together
        parts = command.split()
        if len(parts) > 1:
            # Check if first part is a known command
            known_commands = {"ping", "ls", "cp", "mv", "rm", "mkdir", "touch", "cat", "echo",
                           "gnome-system-monitor", "firefox", "google-chrome", "chromium",
                           "gnome-terminal", "gedit", "nautilus", "gedit"}
            if parts[0] in known_commands or "/" in parts[0]:
                command = parts[0]
                arguments = parts[1:]
                logger.warning(f"LLM returned combined command, split into: {command} + {arguments}")

    logger.info(f"LLM generated command: {command} with {len(arguments)} arguments, method={method}")
    logger.info(f"Explanation: {explanation}")
    
    return {
        "command": command,
        "arguments": arguments,
        "method": method,
        "explanation": explanation
    }


def _generate_simple_plan(state: OperonixState) -> Plan:
    """Generate a plan for simple requests using LLM command generation.
    
    This function uses LangChain to generate concrete commands instead of
    delegating to legacy systems. This removes domain restrictions and allows
    the system to handle any scenario.
    
    Per migration plan §4.2:
    - Planner decides WHAT (goal-oriented steps)
    - Router decides HOW (concrete execution method per step)
    - LLM generates concrete commands (not hardcoded translators)
    
    Args:
        state: Current OperonixState
        
    Returns:
        Plan with LLM-generated concrete command
    """
    import uuid
    from migration.feature_flags import flags
    
    user_input = state.task.user_input
    intent_name = get_intent_name(state, "unknown")
    intent_parameters = get_safe_field(state, 'intent.parameters', {})
    context_dict = None
    
    # Convert ContextSnapshot to dict for LLM
    if state.context:
        from graph.context_helpers import context_to_dict
        context_dict = context_to_dict(state.context)
    
    # Use LLM to generate concrete command
    if flags.USE_LANGCHAIN_MODELS:
        try:
            command_result = run_async_safely(
                _generate_command_with_llm,
                user_input,
                intent_name,
                intent_parameters,
                context_dict,
                timeout=30.0
            )
            
            if command_result:
                # Create PlanStep with concrete command from LLM
                step = PlanStep(
                    step_id=str(uuid.uuid4()),
                    action=command_result["command"],  # Concrete command, not "execute"
                    parameters={
                        "shell_argv": command_result["arguments"],
                        "method": command_result["method"],
                        "explanation": command_result["explanation"],
                        "user_input": user_input  # Keep for reference
                    },
                    objective=f"Execute: {command_result['command']}",
                    idempotency=PlanStepIdempotency.CONDITIONAL,
                    side_effect=PlanStepSideEffect.LOCAL,
                    reversibility=True
                )
                
                logger.info(f"Generated simple plan using LLM command generation: {command_result['command']}")
                return Plan(steps=[step])
        except Exception as e:
            # LLM command generation failed, treat as recoverable
            graph_error = handle_recoverable_error(
                e,
                "create_plan",
                fallback_description="falling back to simple plan without LLM"
            )
            track_error(state, graph_error)
            logger.warning(f"LLM command generation failed: {e}, using fallback")
    
    # Fallback: Create a simple plan without LLM (for when USE_LANGCHAIN_MODELS is false)
    # This fallback is temporary until LLM generation is stable
    logger.info("Using fallback simple plan (LLM command generation unavailable)")
    
    step = PlanStep(
        step_id=str(uuid.uuid4()),
        action="execute",
        parameters={
            "user_input": user_input,
            "intent": intent_name,
            "parameters": intent_parameters
        },
        objective=f"Execute: {user_input}",
        idempotency=PlanStepIdempotency.CONDITIONAL,
        side_effect=PlanStepSideEffect.LOCAL,
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
            # Use run_async_safely to call async LangChain from sync context
            plan = run_async_safely(
                _generate_plan_with_langchain,
                state,
                timeout=30.0
            )
            if plan:
                return plan
        except Exception as e:
            # LangChain plan generation is optional, treat as recoverable
            graph_error = handle_recoverable_error(
                e,
                "create_plan",
                fallback_description="falling back to placeholder complex plan"
            )
            # Note: We don't track this error in state as it's a fallback path
    
    # Fallback to placeholder multi-step plan
    logger.info("Using placeholder complex plan (LangChain unavailable)")
    return _generate_placeholder_complex_plan(state)


async def _generate_plan_with_langchain(state: OperonixState) -> Plan:
    """Use LangChain to generate a plan for complex requests with concrete commands.
    
    This function uses LangChain to generate a multi-step plan where each step
    has a concrete command generated by the LLM, not just high-level actions.
    
    Args:
        state: Current OperonixState
        
    Returns:
        Plan with AI-generated steps with concrete commands
    """
    from ai.models.model_service import model_service
    import uuid
    
    user_input = state.task.user_input
    intent_name = get_intent_name(state, "unknown")
    intent_parameters = get_safe_field(state, 'intent.parameters', {})
    
    # Convert ContextSnapshot to dict for LLM
    context_dict = None
    if state.context:
        from graph.context_helpers import context_to_dict
        context_dict = context_to_dict(state.context)
    
    context_info = ""
    if context_dict:
        app_name = context_dict.get("app_name", "unknown")
        app_type = context_dict.get("app_type", "unknown")
        cwd = context_dict.get("cwd", "unknown")
        context_info = f"\nCurrent context: app={app_name}, type={app_type}, cwd={cwd}"
    
    messages = [
        {
            "role": "system",
            "content": """You are a planning agent for an AI assistant. Break down the user's request into a sequence of executable steps.

Each step should have:
- command: the concrete command to execute (e.g., "gnome-system-monitor", "mkdir -p /tmp/test")
- method: "shell", "api", "plugin", or "ui"
- objective: A clear description of what the step achieves
- arguments: list of command-line arguments (if applicable)

Platform-specific guidance:
- Linux (GNOME/KDE): Use system application names (gnome-system-monitor, nautilus, gedit, etc.)
- macOS: Use open -a for applications
- Windows: Use start or full paths

Respond in JSON format with key: steps (array of step objects)."""
        },
        {
            "role": "user",
            "content": f"User request: {user_input}\nIntent: {intent_name}\nParameters: {intent_parameters}{context_info}"
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
                        "command": {"type": "string"},
                        "method": {"type": "string", "enum": ["shell", "api", "plugin", "ui"]},
                        "objective": {"type": "string"},
                        "arguments": {"type": "array", "items": {"type": "string"}}
                    }
                }
            }
        }
    }
    
    result = await model_service.generate_structured_output(messages, schema)
    
    # Convert LangChain response to PlanStep objects with concrete commands
    steps = []
    for step_data in result.get("steps", []):
        command = step_data.get("command", "")
        method = step_data.get("method", "shell")
        objective = step_data.get("objective", "")
        arguments = step_data.get("arguments", [])
        
        # Ensure arguments is a list of strings
        if not isinstance(arguments, list):
            arguments = []
        arguments = [str(arg) for arg in arguments]
        
        # If no arguments but command has spaces, split it
        if not arguments and " " in command:
            parts = command.split()
            if len(parts) > 1:
                command = parts[0]
                arguments = parts[1:]
        
        step = PlanStep(
            step_id=str(uuid.uuid4()),
            action=command,  # Concrete command
            parameters={
                "shell_argv": arguments,
                "method": method,
                "user_input": user_input  # Keep for reference
            },
            objective=objective,
            idempotency=PlanStepIdempotency.CONDITIONAL,
            side_effect=PlanStepSideEffect.LOCAL,
            reversibility=True
        )
        steps.append(step)
    
    # Add dependencies (simple sequential for now)
    for i in range(1, len(steps)):
        steps[i].dependencies = [steps[i-1].step_id]
    
    logger.info(f"Generated complex plan with {len(steps)} steps using LLM")
    for i, step in enumerate(steps):
        logger.info(f"  Step {i}: {step.action} (method={step.parameters.get('method', 'unknown')})")
    
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
        idempotency="idempotent",
        side_effect="read_only",
        reversibility=True
    )
    
    step2 = PlanStep(
        step_id=str(uuid.uuid4()),
        action="execute_intent",
        arguments={
            "intent": get_intent_name(state, "unknown"),
            "parameters": get_safe_field(state, 'intent.parameters', {})
        },
        objective=f"Execute intent: {state.task.user_input}",
        idempotency="conditional",
        side_effect="local",
        reversibility=True,
        dependencies=[step1.step_id]
    )
    
    return Plan(steps=[step1, step2])
