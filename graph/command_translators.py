"""
Command Translators — Operonix Graph
────────────────────────────────

DEPRECATED: This module is deprecated and will be removed in a future version.

Per migration plan §4.2 and §4.6:
- LLM should generate concrete commands via structured output, not hardcoded translators
- Command translators restrict the domain to predefined commands
- This violates the architectural principle that the solution must work for every scenario

New Implementation:
- LLM command generation now happens in graph/nodes/create_plan.py
- _generate_command_with_llm() function uses LangChain to generate concrete commands
- This allows the system to handle any scenario without domain restrictions

This file is kept temporarily for backward compatibility during migration.
It will be removed once LLM command generation is proven stable.
"""
from __future__ import annotations

import logging
from typing import Dict, Any, Optional

logger = logging.getLogger("Graph.CommandTranslators")

# Deprecation warning
import warnings
warnings.warn(
    "command_translators.py is deprecated. LLM command generation is now in create_plan.py. "
    "This file will be removed in a future version.",
    DeprecationWarning,
    stacklevel=2
)


def translate_intent_to_command(
    intent_name: str,
    intent_parameters: Dict[str, Any],
    method_type: str,
    context: Optional[Dict[str, Any]] = None
) -> Dict[str, Any]:
    """Translate intent to concrete command for execution.
    
    This function provides the translation layer that converts high-level intents
    into concrete commands. For now, it provides basic translations for common intents.
    Future iterations will expand this with more sophisticated translation logic.
    
    Args:
        intent_name: Name of the intent (e.g., "create_file", "open_application")
        intent_parameters: Parameters extracted from intent
        method_type: Execution method (SHELL, API, PLUGIN, UI)
        context: Context information for translation
        
    Returns:
        Dictionary with concrete command and parameters for executor
    """
    logger.info(f"Translating intent '{intent_name}' to command for method '{method_type}'")
    
    # For now, provide a basic translation layer
    # In future iterations, this will use the existing executor's command generation logic
    
    command = {
        "action": intent_name,  # Use intent name as action for now
        "args": intent_parameters,
        "context": context or {}
    }
    
    # Add method-specific transformations
    if method_type == "SHELL":
        # For shell, convert to command-line format
        if intent_parameters:
            command["argv"] = _build_shell_argv(intent_name, intent_parameters)
    elif method_type == "API":
        # For API, structure as HTTP request
        command["http"] = _build_api_request(intent_name, intent_parameters)
    elif method_type == "PLUGIN":
        # For plugin, structure as plugin call
        command["plugin"] = _build_plugin_call(intent_name, intent_parameters)
    elif method_type == "UI":
        # For UI, structure as UI automation
        command["ui"] = _build_ui_automation(intent_name, intent_parameters, context)
    
    return command


def _build_shell_argv(intent_name: str, parameters: Dict[str, Any]) -> list[str]:
    """Build shell command arguments from intent and parameters.
    
    Args:
        intent_name: Name of the intent
        parameters: Intent parameters
        
    Returns:
        List of command-line arguments
    """
    # Basic shell command construction
    # In future, this will use the existing executor's command generation logic
    
    if intent_name == "create_file":
        path = parameters.get("path", "")
        return ["touch", path]
    elif intent_name == "create_dir":
        path = parameters.get("path", "")
        return ["mkdir", "-p", path]
    elif intent_name == "delete_file":
        path = parameters.get("path", "")
        return ["rm", path]
    elif intent_name == "open_application":
        app_name = parameters.get("app_name", "")
        return ["open", "-a", app_name]
    else:
        # Generic: use intent name as command
        return [intent_name]


def _build_api_request(intent_name: str, parameters: Dict[str, Any]) -> Dict[str, Any]:
    """Build API request from intent and parameters.
    
    Args:
        intent_name: Name of the intent
        parameters: Intent parameters
        
    Returns:
        Dictionary with HTTP request details
    """
    # Basic API request construction
    return {
        "method": "POST",
        "endpoint": f"/api/{intent_name}",
        "body": parameters
    }


def _build_plugin_call(intent_name: str, parameters: Dict[str, Any]) -> Dict[str, Any]:
    """Build plugin call from intent and parameters.
    
    Args:
        intent_name: Name of the intent
        parameters: Intent parameters
        
    Returns:
        Dictionary with plugin call details
    """
    # Basic plugin call construction
    return {
        "plugin_name": intent_name,
        "method": "execute",
        "arguments": parameters
    }


def _build_ui_automation(intent_name: str, parameters: Dict[str, Any], context: Dict[str, Any]) -> Dict[str, Any]:
    """Build UI automation from intent and parameters.
    
    Args:
        intent_name: Name of the intent
        parameters: Intent parameters
        context: Context information
        
    Returns:
        Dictionary with UI automation details
    """
    # Basic UI automation construction
    automation = {
        "action": intent_name,
        "parameters": parameters
    }
    
    # Add context information if available
    if context:
        automation["context"] = context
    
    return automation
