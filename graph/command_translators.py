"""
Command Translators — Operonix Graph
────────────────────────────────

Translates intents to concrete commands for execution.
This module provides the translation layer that converts high-level intents
into concrete commands for different execution methods (SHELL, API, PLUGIN, UI).
"""
from __future__ import annotations

import logging
from typing import Dict, Any, Optional

logger = logging.getLogger("Graph.CommandTranslators")


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
