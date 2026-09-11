"""
LangChain Tool Wrappers — Operonix Graph
──────────────────────────────────────

LangChain Tool wrappers for Operonix capabilities.
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

These wrappers expose Operonix capabilities as LangChain tools while ensuring
execution goes through Operonix's safety and executor boundaries.
"""
from __future__ import annotations

import logging
from typing import Any, Dict, Optional, List

logger = logging.getLogger("Graph.LangChainTools")


class OperonixLangChainTool:
    """LangChain-compatible wrapper for Operonix tools.
    
    This wrapper exposes Operonix capabilities as LangChain tools while ensuring
    execution goes through Operonix's safety and executor boundaries.
    
    Per migration plan Phase 11: Tool Adapter Architecture
    """
    
    def __init__(self, tool_adapter):
        """Initialize the LangChain tool wrapper.
        
        Args:
            tool_adapter: OperonixToolAdapter instance
        """
        self.tool_adapter = tool_adapter
        self.schema = tool_adapter.get_schema()
        logger.info(f"OperonixLangChainTool initialized: {self.schema['name']}")
    
    @property
    def name(self) -> str:
        """Get the tool name.
        
        Returns:
            Tool name
        """
        return self.schema["name"]
    
    @property
    def description(self) -> str:
        """Get the tool description.
        
        Returns:
            Tool description
        """
        return self.schema["description"]
    
    @property
    def parameters(self) -> Dict[str, Any]:
        """Get the tool parameters schema.
        
        Returns:
            Parameters schema
        """
        return self.schema.get("parameters", {})
    
    def run(self, **kwargs) -> str:
        """Run the tool through Operonix's adapter.
        
        This method is called by LangChain when the tool is invoked.
        
        Args:
            **kwargs: Tool execution parameters
            
        Returns:
            String result (LangChain expects string output)
        """
        logger.info(f"Running LangChain tool: {self.name}")
        
        # Execute through Operonix's adapter
        result = self.tool_adapter.execute(**kwargs)
        
        # Convert result to string for LangChain
        if result.get("success"):
            return str(result.get("result", "Success"))
        else:
            return f"Error: {result.get('error', 'Unknown error')}"
    
    async def arun(self, **kwargs) -> str:
        """Async run the tool through Operonix's adapter.
        
        This method is called by LangChain when the tool is invoked asynchronously.
        
        Args:
            **kwargs: Tool execution parameters
            
        Returns:
            String result (LangChain expects string output)
        """
        logger.info(f"Async running LangChain tool: {self.name}")
        
        # For now, just call the synchronous run
        # A future implementation may support async execution
        return self.run(**kwargs)


class LangChainToolFactory:
    """Factory for creating LangChain tools from Operonix adapters.
    
    This factory provides a convenient way to create LangChain-compatible
    tools from Operonix tool adapters.
    """
    
    def __init__(self):
        """Initialize the LangChain tool factory."""
        logger.info("LangChainToolFactory initialized")
    
    def create_tool(self, tool_adapter) -> OperonixLangChainTool:
        """Create a LangChain tool from an Operonix adapter.
        
        Args:
            tool_adapter: OperonixToolAdapter instance
            
        Returns:
            OperonixLangChainTool instance
        """
        return OperonixLangChainTool(tool_adapter)
    
    def create_tools_from_registry(self, tool_adapter_registry) -> List[OperonixLangChainTool]:
        """Create LangChain tools from all adapters in a registry.
        
        Args:
            tool_adapter_registry: ToolAdapterRegistry instance
            
        Returns:
            List of OperonixLangChainTool instances
        """
        tools = []
        
        for tool_id in tool_adapter_registry.list_adapters():
            adapter = tool_adapter_registry.get_adapter(tool_id)
            if adapter:
                tool = self.create_tool(adapter)
                tools.append(tool)
        
        logger.info(f"Created {len(tools)} LangChain tools from registry")
        
        return tools
    
    def create_tool_list(self, tool_adapters: List) -> List[OperonixLangChainTool]:
        """Create LangChain tools from a list of adapters.
        
        Args:
            tool_adapters: List of OperonixToolAdapter instances
            
        Returns:
            List of OperonixLangChainTool instances
        """
        tools = [self.create_tool(adapter) for adapter in tool_adapters]
        
        logger.info(f"Created {len(tools)} LangChain tools from list")
        
        return tools


# Global LangChain tool factory instance
_langchain_tool_factory: Optional[LangChainToolFactory] = None


def get_langchain_tool_factory() -> LangChainToolFactory:
    """Get the global LangChain tool factory instance.
    
    Returns:
        LangChainToolFactory instance
    """
    global _langchain_tool_factory
    
    if _langchain_tool_factory is None:
        _langchain_tool_factory = LangChainToolFactory()
    
    return _langchain_tool_factory


def get_langchain_tools() -> List[OperonixLangChainTool]:
    """Get all LangChain tools from the global registry.
    
    Returns:
        List of OperonixLangChainTool instances
    """
    from graph.tool_adapter import get_tool_adapter_registry
    
    factory = get_langchain_tool_factory()
    registry = get_tool_adapter_registry()
    
    return factory.create_tools_from_registry(registry)
