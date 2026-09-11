"""
Operonix Tool Adapter — Operonix Graph
────────────────────────────────────

Operonix Tool Adapter for LangChain integration.
Per migration plan Phase 11: Tool Adapter Architecture

Architecture:
```
LangChain Tool
      ↓
Operonix Tool Adapter
      ↓
BaseTool
      ↓
Safety / Executor
      ↓
Capability / Plugin
```

The existing BaseTool, ToolRegistry, capabilities, and plugins remain the
implementation foundation. The adapter exposes Operonix capabilities to LangChain
without bypassing Operonix controls.
"""
from __future__ import annotations

import logging
from typing import Any, Dict, Optional, Type
from abc import ABC, abstractmethod

logger = logging.getLogger("Graph.ToolAdapter")


class OperonixToolAdapter(ABC):
    """Base class for Operonix tool adapters.
    
    This adapter wraps Operonix capabilities for LangChain integration while
    ensuring execution goes through Operonix's safety and executor boundaries.
    
    Per migration plan Phase 11: Tool Adapter Architecture
    """
    
    def __init__(self, tool_id: str, capability_id: str):
        """Initialize the tool adapter.
        
        Args:
            tool_id: Tool identifier
            capability_id: Capability identifier
        """
        self.tool_id = tool_id
        self.capability_id = capability_id
        logger.info(f"OperonixToolAdapter initialized: {tool_id} -> {capability_id}")
    
    @abstractmethod
    def execute(self, **kwargs) -> Dict[str, Any]:
        """Execute the tool through Operonix's safety and executor boundaries.
        
        This method must:
        1. Validate inputs through Operonix's safety checks
        2. Execute through Operonix's executor
        3. Return results in Operonix's format
        
        Args:
            **kwargs: Tool execution parameters
            
        Returns:
            Dict with execution results
        """
        pass
    
    @abstractmethod
    def get_schema(self) -> Dict[str, Any]:
        """Get the tool schema for LangChain integration.
        
        Returns:
            Dict with tool schema (name, description, parameters)
        """
        pass
    
    def validate_input(self, input_data: Dict[str, Any]) -> bool:
        """Validate input data before execution.
        
        This is a hook for subclasses to implement input validation.
        
        Args:
            input_data: Input data to validate
            
        Returns:
            True if input is valid, False otherwise
        """
        # Default implementation: always valid
        return True
    
    def sanitize_output(self, output_data: Dict[str, Any]) -> Dict[str, Any]:
        """Sanitize output data before returning to LangChain.
        
        This is a hook for subclasses to implement output sanitization.
        
        Args:
            output_data: Output data to sanitize
            
        Returns:
            Sanitized output data
        """
        # Default implementation: return as-is
        return output_data


class BaseToolAdapter(OperonixToolAdapter):
    """Base tool adapter for Operonix BaseTool integration.
    
    This adapter wraps Operonix's BaseTool for LangChain integration.
    """
    
    def __init__(self, tool_id: str, capability_id: str, base_tool: Any):
        """Initialize the base tool adapter.
        
        Args:
            tool_id: Tool identifier
            capability_id: Capability identifier
            base_tool: Operonix BaseTool instance
        """
        super().__init__(tool_id, capability_id)
        self.base_tool = base_tool
        logger.info(f"BaseToolAdapter initialized: {tool_id} -> {base_tool}")
    
    def execute(self, **kwargs) -> Dict[str, Any]:
        """Execute the tool through Operonix's BaseTool.
        
        Args:
            **kwargs: Tool execution parameters
            
        Returns:
            Dict with execution results
        """
        logger.info(f"Executing tool {self.tool_id} through BaseTool")
        
        # Validate input
        if not self.validate_input(kwargs):
            return {
                "success": False,
                "error": "Input validation failed",
                "tool_id": self.tool_id
            }
        
        try:
            # Execute through BaseTool
            result = self.base_tool.execute(**kwargs)
            
            # Sanitize output
            sanitized_result = self.sanitize_output(result)
            
            return {
                "success": True,
                "result": sanitized_result,
                "tool_id": self.tool_id
            }
        except Exception as e:
            logger.error(f"Error executing tool {self.tool_id}: {e}")
            return {
                "success": False,
                "error": str(e),
                "tool_id": self.tool_id
            }
    
    def get_schema(self) -> Dict[str, Any]:
        """Get the tool schema for LangChain integration.
        
        Returns:
            Dict with tool schema
        """
        schema = {
            "name": self.tool_id,
            "description": getattr(self.base_tool, "description", f"Operonix tool: {self.tool_id}"),
            "parameters": getattr(self.base_tool, "parameters", {})
        }
        
        return schema


class PluginAdapter(OperonixToolAdapter):
    """Plugin adapter for Operonix plugin integration.
    
    This adapter wraps Operonix plugins for LangChain integration.
    """
    
    def __init__(self, tool_id: str, capability_id: str, plugin: Any):
        """Initialize the plugin adapter.
        
        Args:
            tool_id: Tool identifier
            capability_id: Capability identifier
            plugin: Operonix plugin instance
        """
        super().__init__(tool_id, capability_id)
        self.plugin = plugin
        logger.info(f"PluginAdapter initialized: {tool_id} -> {plugin}")
    
    def execute(self, **kwargs) -> Dict[str, Any]:
        """Execute the plugin through Operonix's plugin system.
        
        Args:
            **kwargs: Plugin execution parameters
            
        Returns:
            Dict with execution results
        """
        logger.info(f"Executing plugin {self.tool_id}")
        
        # Validate input
        if not self.validate_input(kwargs):
            return {
                "success": False,
                "error": "Input validation failed",
                "tool_id": self.tool_id
            }
        
        try:
            # Execute through plugin
            result = self.plugin.execute(**kwargs)
            
            # Sanitize output
            sanitized_result = self.sanitize_output(result)
            
            return {
                "success": True,
                "result": sanitized_result,
                "tool_id": self.tool_id
            }
        except Exception as e:
            logger.error(f"Error executing plugin {self.tool_id}: {e}")
            return {
                "success": False,
                "error": str(e),
                "tool_id": self.tool_id
            }
    
    def get_schema(self) -> Dict[str, Any]:
        """Get the plugin schema for LangChain integration.
        
        Returns:
            Dict with plugin schema
        """
        schema = {
            "name": self.tool_id,
            "description": getattr(self.plugin, "description", f"Operonix plugin: {self.tool_id}"),
            "parameters": getattr(self.plugin, "parameters", {})
        }
        
        return schema


class CapabilityAdapter(OperonixToolAdapter):
    """Capability adapter for Operonix capability integration.
    
    This adapter wraps Operonix capabilities for LangChain integration.
    """
    
    def __init__(self, tool_id: str, capability_id: str, capability: Any):
        """Initialize the capability adapter.
        
        Args:
            tool_id: Tool identifier
            capability_id: Capability identifier
            capability: Operonix capability instance
        """
        super().__init__(tool_id, capability_id)
        self.capability = capability
        logger.info(f"CapabilityAdapter initialized: {tool_id} -> {capability}")
    
    def execute(self, **kwargs) -> Dict[str, Any]:
        """Execute the capability through Operonix's capability system.
        
        Args:
            **kwargs: Capability execution parameters
            
        Returns:
            Dict with execution results
        """
        logger.info(f"Executing capability {self.tool_id}")
        
        # Validate input
        if not self.validate_input(kwargs):
            return {
                "success": False,
                "error": "Input validation failed",
                "tool_id": self.tool_id
            }
        
        try:
            # Execute through capability
            result = self.capability.execute(**kwargs)
            
            # Sanitize output
            sanitized_result = self.sanitize_output(result)
            
            return {
                "success": True,
                "result": sanitized_result,
                "tool_id": self.tool_id
            }
        except Exception as e:
            logger.error(f"Error executing capability {self.tool_id}: {e}")
            return {
                "success": False,
                "error": str(e),
                "tool_id": self.tool_id
            }
    
    def get_schema(self) -> Dict[str, Any]:
        """Get the capability schema for LangChain integration.
        
        Returns:
            Dict with capability schema
        """
        schema = {
            "name": self.tool_id,
            "description": getattr(self.capability, "description", f"Operonix capability: {self.tool_id}"),
            "parameters": getattr(self.capability, "parameters", {})
        }
        
        return schema


class ToolAdapterRegistry:
    """Registry for Operonix tool adapters.
    
    This registry manages tool adapters and provides access to them for
    LangChain integration.
    """
    
    def __init__(self):
        """Initialize the tool adapter registry."""
        self.adapters: Dict[str, OperonixToolAdapter] = {}
        logger.info("ToolAdapterRegistry initialized")
    
    def register_adapter(self, adapter: OperonixToolAdapter) -> None:
        """Register a tool adapter.
        
        Args:
            adapter: Tool adapter to register
        """
        self.adapters[adapter.tool_id] = adapter
        logger.info(f"Registered tool adapter: {adapter.tool_id}")
    
    def get_adapter(self, tool_id: str) -> Optional[OperonixToolAdapter]:
        """Get a tool adapter by tool ID.
        
        Args:
            tool_id: Tool identifier
            
        Returns:
            Tool adapter or None if not found
        """
        return self.adapters.get(tool_id)
    
    def list_adapters(self) -> list[str]:
        """List all registered tool adapters.
        
        Returns:
            List of tool IDs
        """
        return list(self.adapters.keys())
    
    def get_all_schemas(self) -> Dict[str, Dict[str, Any]]:
        """Get schemas for all registered adapters.
        
        Returns:
            Dict mapping tool IDs to schemas
        """
        schemas = {}
        for tool_id, adapter in self.adapters.items():
            schemas[tool_id] = adapter.get_schema()
        return schemas


# Global tool adapter registry instance
_tool_adapter_registry: Optional[ToolAdapterRegistry] = None


def get_tool_adapter_registry() -> ToolAdapterRegistry:
    """Get the global tool adapter registry instance.
    
    Returns:
        ToolAdapterRegistry instance
    """
    global _tool_adapter_registry
    
    if _tool_adapter_registry is None:
        _tool_adapter_registry = ToolAdapterRegistry()
    
    return _tool_adapter_registry
