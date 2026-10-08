"""
Analyze Intent Node — Operonix Graph
────────────────────────────────────

Analyze intent node: First major LangChain integration point.
Per migration plan §4.2, node 3:
"analyze_intent — first major LangChain integration point. LangChain does AI
interpretation → existing IntentParser's deterministic resolution/validation/
keyword-fallback logic is preserved on top."
"""
from __future__ import annotations

import logging
from typing import Dict, Any

from migration.graph_state import OperonixState
from migration.domain_contracts import IntentResult
from migration.feature_flags import flags
from graph.trace_collector import get_trace_collector
from graph.async_helpers import run_async_safely

logger = logging.getLogger("Graph.AnalyzeIntent")


def analyze_intent_node(state: OperonixState) -> Dict[str, Any]:
    """Analyze intent node: Parse user intent using LangChain.
    
    This node:
    - Uses LangChain for AI interpretation of user input
    - Preserves existing IntentParser's deterministic resolution/validation
    - Preserves keyword-fallback logic
    - Creates IntentResult with confidence and parameters
    
    Args:
        state: Current OperonixState
        
    Returns:
        Dict with updated state including intent
    """
    logger.info(f"ANALYZE_INTENT: Analyzing intent for task {state.task.task_id}")
    
    state.add_history_event("analyze_intent_started", {
        "task_id": state.task.task_id,
        "user_input": state.task.user_input
    })
    
    # Use LangChain if enabled, otherwise use placeholder
    if flags.USE_LANGCHAIN_MODELS:
        intent_result = _analyze_intent_with_langchain(state)
    else:
        intent_result = _analyze_intent_placeholder(state)
    
    # Apply deterministic resolution/validation from existing IntentParser
    # This preserves the existing logic on top of LangChain interpretation
    intent_result = _apply_deterministic_resolution(intent_result, state)
    
    # Apply keyword-fallback logic
    intent_result = _apply_keyword_fallback(intent_result, state)
    
    state.intent = intent_result
    
    # Phase 9: Collect trace event for intent
    trace_collector = get_trace_collector()
    trace_collector.collect_intent(
        task_id=state.task.task_id,
        intent_name=intent_result.name,
        intent_parameters=intent_result.parameters
    )
    
    state.add_history_event("analyze_intent_completed", {
        "task_id": state.task.task_id,
        "intent_name": intent_result.name,
        "confidence": intent_result.confidence
    })
    
    state.update_timestamp()
    
    return {"intent": intent_result}


def _analyze_intent_with_langchain(state: OperonixState) -> IntentResult:
    """Analyze intent using LangChain model.
    
    Args:
        state: Current OperonixState
        
    Returns:
        IntentResult with AI-interpreted intent
    """
    try:
        from ai.models.model_service import model_service
        
        if not model_service.is_available():
            logger.warning("LangChain model not available, falling back to placeholder")
            return _analyze_intent_placeholder(state)
        
        # Build prompt for intent analysis
        messages = [
            {
                "role": "system",
                "content": """You are an intent analyzer for an AI agent. Analyze the user's request and determine:
1. The primary intent (e.g., open_application, create_file, search_web, execute_command)
2. Confidence level (0.0 to 1.0)
3. Key parameters (e.g., application name, file path, search query)

Respond in JSON format with keys: intent_name, confidence, parameters."""
            },
            {
                "role": "user",
                "content": state.task.user_input
            }
        ]
        
        # Use structured output
        schema = {
            "name": "intent_analysis",
            "properties": {
                "intent_name": {"type": "string"},
                "confidence": {"type": "number"},
                "parameters": {"type": "object"}
            }
        }
        
        # Use run_async_safely to call async LangChain from sync context
        result = run_async_safely(
            model_service.generate_structured_output,
            messages,
            schema,
            timeout=30.0
        )
        
        # Create IntentResult from LangChain response
        intent_result = IntentResult(
            name=result.get("intent_name", "unknown"),
            confidence=result.get("confidence", 0.5),
            parameters=result.get("parameters", {}),
            raw_intent=state.task.user_input,
            fallback_used=False
        )
        
        logger.info(f"LangChain intent analysis: {intent_result.name} (confidence: {intent_result.confidence})")
        return intent_result
        
    except Exception as e:
        logger.error(f"LangChain intent analysis failed: {e}")
        # Fallback to placeholder on error
        return _analyze_intent_placeholder(state)


def _analyze_intent_placeholder(state: OperonixState) -> IntentResult:
    """Analyze intent using placeholder logic (fallback).
    
    Args:
        state: Current OperonixState
        
    Returns:
        IntentResult with placeholder intent
    """
    logger.info("Using placeholder intent analysis")
    
    # Simple keyword-based intent detection as fallback
    user_input = state.task.user_input.lower()
    original_input = state.task.user_input
    parameters = {"user_input": original_input}
    
    if "open" in user_input and ("app" in user_input or "firefox" in user_input or "chrome" in user_input):
        intent_name = "open_application"
        
        # Extract app name from natural language
        # Patterns: "open the app named Clocks", "open Clocks", "launch Firefox"
        import re
        
        # Pattern 1: "open the app named <app_name>"
        match = re.search(r'open the app named\s+(\w+)', original_input, re.IGNORECASE)
        if match:
            parameters["app_name"] = match.group(1)
        # Pattern 2: "open <app_name>" (capture until next keyword)
        elif user_input.startswith("open "):
            # Extract the app name after "open"
            parts = original_input.split("open ", 1)[1].strip()
            # Take first word or phrase (simplified)
            app_name = parts.split()[0] if parts else ""
            if app_name:
                parameters["app_name"] = app_name
        # Pattern 3: "launch <app_name>"
        elif user_input.startswith("launch "):
            parts = original_input.split("launch ", 1)[1].strip()
            app_name = parts.split()[0] if parts else ""
            if app_name:
                parameters["app_name"] = app_name
        # Pattern 4: Direct app mentions
        elif "firefox" in user_input:
            parameters["app_name"] = "firefox"
        elif "chrome" in user_input:
            parameters["app_name"] = "chrome"
        elif "system monitor" in user_input:
            parameters["app_name"] = "system monitor"
            
    elif "create" in user_input and "file" in user_input:
        intent_name = "create_file"
    elif "delete" in user_input and "file" in user_input:
        intent_name = "delete_file"
    elif "search" in user_input:
        intent_name = "search_web"
    elif "execute" in user_input or "run" in user_input:
        intent_name = "execute_command"
    else:
        intent_name = "unknown"
    
    return IntentResult(
        name=intent_name,
        confidence=0.6,  # Lower confidence for placeholder
        parameters=parameters,
        raw_intent=original_input,
        fallback_used=True
    )


def _apply_deterministic_resolution(intent_result: IntentResult, state: OperonixState) -> IntentResult:
    """Apply deterministic resolution from existing IntentParser.
    
    This preserves the existing deterministic resolution/validation logic
    on top of LangChain interpretation.
    
    In a full implementation, this would integrate with brain/intent_parser.py.
    For now, we apply basic validation.
    
    Args:
        intent_result: IntentResult from LangChain or placeholder
        state: Current OperonixState
        
    Returns:
        IntentResult with deterministic resolution applied
    """
    # In a full implementation, this would call existing IntentParser
    # For now, we apply basic validation
    
    # Validate intent name
    valid_intents = [
        "open_application", "create_file", "delete_file", "search_web",
        "execute_command", "navigate", "click", "type", "unknown"
    ]
    
    if intent_result.name not in valid_intents:
        logger.warning(f"Unknown intent '{intent_result.name}', defaulting to 'unknown'")
        intent_result.name = "unknown"
        intent_result.confidence = min(intent_result.confidence, 0.5)
    
    # Ensure app_name is set for open_application intent (plugin requirement)
    if intent_result.name == "open_application" and "app_name" not in intent_result.parameters:
        # Try to extract from application parameter
        if "application" in intent_result.parameters:
            intent_result.parameters["app_name"] = intent_result.parameters["application"]
        else:
            # Try to extract from user_input
            user_input = state.task.user_input.lower()
            if "firefox" in user_input:
                intent_result.parameters["app_name"] = "firefox"
            elif "chrome" in user_input:
                intent_result.parameters["app_name"] = "chrome"
            elif "system monitor" in user_input:
                intent_result.parameters["app_name"] = "system monitor"
            else:
                # Last resort: set to generic value
                logger.warning("Could not extract app_name, setting to generic value")
                intent_result.parameters["app_name"] = "unknown"
    
    return intent_result


def _apply_keyword_fallback(intent_result: IntentResult, state: OperonixState) -> IntentResult:
    """Apply keyword-fallback logic from existing IntentParser.
    
    This preserves the existing keyword-fallback logic on top of
    LangChain interpretation.
    
    This integrates with brain/intent_parser.py keyword logic.
    
    Args:
        intent_result: IntentResult from LangChain or placeholder
        state: Current OperonixState
        
    Returns:
        IntentResult with keyword fallback applied
    """
    try:
        from brain.intent_parser import _BRIDGE_KEYWORDS, _PANEL_SUDO_KEYWORDS, _LAB_KEYWORDS
        
        user_input = state.task.user_input.lower()
        original_input = state.task.user_input
        
        # Apply keyword overrides from existing IntentParser
        # Bridge keywords (must run in user's shell)
        for keyword in _BRIDGE_KEYWORDS:
            if keyword in user_input:
                intent_result.parameters["profile_hint"] = "bridge"
                logger.info(f"Bridge keyword '{keyword}' detected, setting profile_hint=bridge")
                break
        
        # Panel sudo keywords (require password prompt)
        for keyword in _PANEL_SUDO_KEYWORDS:
            if keyword in user_input:
                intent_result.parameters["profile_hint"] = "panel_sudo"
                logger.info(f"Panel sudo keyword '{keyword}' detected, setting profile_hint=panel_sudo")
                break
        
        # Lab keywords (benefit from visible terminal)
        for keyword in _LAB_KEYWORDS:
            if keyword in user_input:
                intent_result.parameters["profile_hint"] = "lab"
                logger.info(f"Lab keyword '{keyword}' detected, setting profile_hint=lab")
                break
        
        # Application-specific keyword overrides
        # Also ensure app_name is set for plugin compatibility
        if "firefox" in user_input:
            intent_result.name = "open_application"
            intent_result.parameters["application"] = "firefox"
            intent_result.parameters["app_name"] = "firefox"
        elif "chrome" in user_input:
            intent_result.name = "open_application"
            intent_result.parameters["application"] = "chrome"
            intent_result.parameters["app_name"] = "chrome"
        elif "system monitor" in user_input:
            intent_result.name = "open_application"
            intent_result.parameters["app_name"] = "system monitor"
            
    except ImportError:
        logger.warning("Could not import IntentParser keywords, using basic keyword overrides")
        # Fallback to basic keyword overrides
        user_input = state.task.user_input.lower()
        
        if "firefox" in user_input:
            intent_result.name = "open_application"
            intent_result.parameters["application"] = "firefox"
            intent_result.parameters["app_name"] = "firefox"
        elif "chrome" in user_input:
            intent_result.name = "open_application"
            intent_result.parameters["application"] = "chrome"
            intent_result.parameters["app_name"] = "chrome"
        elif "system monitor" in user_input:
            intent_result.name = "open_application"
            intent_result.parameters["app_name"] = "system monitor"
    
    return intent_result
