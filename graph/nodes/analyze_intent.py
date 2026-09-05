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
    
    state.add_history_event("analyze_intent_completed", {
        "task_id": state.task.task_id,
        "intent_name": intent_result.name,
        "confidence": intent_result.confidence
    })
    
    state.update_timestamp()
    
    return {"state": state}


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
        
        import asyncio
        result = asyncio.run(model_service.generate_structured_output(messages, schema))
        
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
    
    if "open" in user_input and ("app" in user_input or "firefox" in user_input or "chrome" in user_input):
        intent_name = "open_application"
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
        parameters={"user_input": state.task.user_input},
        raw_intent=state.task.user_input,
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
    
    return intent_result


def _apply_keyword_fallback(intent_result: IntentResult, state: OperonixState) -> IntentResult:
    """Apply keyword-fallback logic from existing IntentParser.
    
    This preserves the existing keyword-fallback logic on top of
    LangChain interpretation.
    
    In a full implementation, this would integrate with brain/intent_parser.py.
    For now, we apply basic keyword overrides.
    
    Args:
        intent_result: IntentResult from LangChain or placeholder
        state: Current OperonixState
        
    Returns:
        IntentResult with keyword fallback applied
    """
    # In a full implementation, this would call existing IntentParser keyword logic
    # For now, we apply basic keyword overrides
    
    user_input = state.task.user_input.lower()
    
    # Keyword overrides (higher priority than AI interpretation)
    if "firefox" in user_input:
        intent_result.name = "open_application"
        intent_result.parameters["application"] = "firefox"
    elif "chrome" in user_input:
        intent_result.name = "open_application"
        intent_result.parameters["application"] = "chrome"
    
    return intent_result
