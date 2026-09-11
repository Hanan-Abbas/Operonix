"""
Tool Adapter Architecture Tests — Operonix Migration Phase 11
────────────────────────────────────────────────────────────

Tests for tool adapter architecture.
Per migration plan Phase 11: Tool Adapter Architecture
"""
from __future__ import annotations

import pytest


# ─── OPERONIX TOOL ADAPTER TESTS ───────────────────────────────────────────

def test_operonix_tool_adapter_abstract():
    """Test that OperonixToolAdapter is abstract and cannot be instantiated directly."""
    from graph.tool_adapter import OperonixToolAdapter
    
    with pytest.raises(TypeError):
        OperonixToolAdapter("test_tool", "test_capability")


def test_operonix_tool_adapter_validate_input_default():
    """Test that default validate_input always returns True."""
    from graph.tool_adapter import OperonixToolAdapter
    
    class ConcreteAdapter(OperonixToolAdapter):
        def execute(self, **kwargs):
            return {"success": True}
        
        def get_schema(self):
            return {"name": "test", "description": "test", "parameters": {}}
    
    adapter = ConcreteAdapter("test_tool", "test_capability")
    
    assert adapter.validate_input({}) is True
    assert adapter.validate_input({"any": "input"}) is True


def test_operonix_tool_adapter_sanitize_output_default():
    """Test that default sanitize_output returns input as-is."""
    from graph.tool_adapter import OperonixToolAdapter
    
    class ConcreteAdapter(OperonixToolAdapter):
        def execute(self, **kwargs):
            return {"success": True}
        
        def get_schema(self):
            return {"name": "test", "description": "test", "parameters": {}}
    
    adapter = ConcreteAdapter("test_tool", "test_capability")
    
    output = {"result": "test"}
    sanitized = adapter.sanitize_output(output)
    
    assert sanitized == output


# ─── BASE TOOL ADAPTER TESTS ──────────────────────────────────────────────────

def test_base_tool_adapter_initialization():
    """Test that BaseToolAdapter can be initialized."""
    from graph.tool_adapter import BaseToolAdapter
    
    # Mock base tool
    class MockBaseTool:
        description = "Mock tool"
        parameters = {}
        
        def execute(self, **kwargs):
            return {"result": "success"}
    
    base_tool = MockBaseTool()
    adapter = BaseToolAdapter("test_tool", "test_capability", base_tool)
    
    assert adapter.tool_id == "test_tool"
    assert adapter.capability_id == "test_capability"
    assert adapter.base_tool == base_tool


def test_base_tool_adapter_execute_success():
    """Test that BaseToolAdapter executes successfully."""
    from graph.tool_adapter import BaseToolAdapter
    
    class MockBaseTool:
        description = "Mock tool"
        parameters = {}
        
        def execute(self, **kwargs):
            return {"result": "success"}
    
    base_tool = MockBaseTool()
    adapter = BaseToolAdapter("test_tool", "test_capability", base_tool)
    
    result = adapter.execute(param="value")
    
    assert result["success"] is True
    assert result["result"] == {"result": "success"}
    assert result["tool_id"] == "test_tool"


def test_base_tool_adapter_execute_error():
    """Test that BaseToolAdapter handles execution errors."""
    from graph.tool_adapter import BaseToolAdapter
    
    class MockBaseTool:
        description = "Mock tool"
        parameters = {}
        
        def execute(self, **kwargs):
            raise Exception("Execution failed")
    
    base_tool = MockBaseTool()
    adapter = BaseToolAdapter("test_tool", "test_capability", base_tool)
    
    result = adapter.execute(param="value")
    
    assert result["success"] is False
    assert "error" in result
    assert result["tool_id"] == "test_tool"


def test_base_tool_adapter_validate_input_failure():
    """Test that BaseToolAdapter respects validate_input."""
    from graph.tool_adapter import BaseToolAdapter
    
    class MockBaseTool:
        description = "Mock tool"
        parameters = {}
        
        def execute(self, **kwargs):
            return {"result": "success"}
    
    base_tool = MockBaseTool()
    adapter = BaseToolAdapter("test_tool", "test_capability", base_tool)
    
    # Override validate_input to fail
    adapter.validate_input = lambda x: False
    
    result = adapter.execute(param="value")
    
    assert result["success"] is False
    assert result["error"] == "Input validation failed"


def test_base_tool_adapter_get_schema():
    """Test that BaseToolAdapter returns correct schema."""
    from graph.tool_adapter import BaseToolAdapter
    
    class MockBaseTool:
        description = "Mock tool description"
        parameters = {"param1": {"type": "string"}}
        
        def execute(self, **kwargs):
            return {"result": "success"}
    
    base_tool = MockBaseTool()
    adapter = BaseToolAdapter("test_tool", "test_capability", base_tool)
    
    schema = adapter.get_schema()
    
    assert schema["name"] == "test_tool"
    assert schema["description"] == "Mock tool description"
    assert schema["parameters"] == {"param1": {"type": "string"}}


# ─── PLUGIN ADAPTER TESTS ───────────────────────────────────────────────────

def test_plugin_adapter_initialization():
    """Test that PluginAdapter can be initialized."""
    from graph.tool_adapter import PluginAdapter
    
    class MockPlugin:
        description = "Mock plugin"
        parameters = {}
        
        def execute(self, **kwargs):
            return {"result": "success"}
    
    plugin = MockPlugin()
    adapter = PluginAdapter("test_plugin", "test_capability", plugin)
    
    assert adapter.tool_id == "test_plugin"
    assert adapter.capability_id == "test_capability"
    assert adapter.plugin == plugin


def test_plugin_adapter_execute_success():
    """Test that PluginAdapter executes successfully."""
    from graph.tool_adapter import PluginAdapter
    
    class MockPlugin:
        description = "Mock plugin"
        parameters = {}
        
        def execute(self, **kwargs):
            return {"result": "success"}
    
    plugin = MockPlugin()
    adapter = PluginAdapter("test_plugin", "test_capability", plugin)
    
    result = adapter.execute(param="value")
    
    assert result["success"] is True
    assert result["result"] == {"result": "success"}
    assert result["tool_id"] == "test_plugin"


def test_plugin_adapter_get_schema():
    """Test that PluginAdapter returns correct schema."""
    from graph.tool_adapter import PluginAdapter
    
    class MockPlugin:
        description = "Mock plugin description"
        parameters = {"param1": {"type": "string"}}
        
        def execute(self, **kwargs):
            return {"result": "success"}
    
    plugin = MockPlugin()
    adapter = PluginAdapter("test_plugin", "test_capability", plugin)
    
    schema = adapter.get_schema()
    
    assert schema["name"] == "test_plugin"
    assert schema["description"] == "Mock plugin description"
    assert schema["parameters"] == {"param1": {"type": "string"}}


# ─── CAPABILITY ADAPTER TESTS ───────────────────────────────────────────────

def test_capability_adapter_initialization():
    """Test that CapabilityAdapter can be initialized."""
    from graph.tool_adapter import CapabilityAdapter
    
    class MockCapability:
        description = "Mock capability"
        parameters = {}
        
        def execute(self, **kwargs):
            return {"result": "success"}
    
    capability = MockCapability()
    adapter = CapabilityAdapter("test_capability", "test_capability_id", capability)
    
    assert adapter.tool_id == "test_capability"
    assert adapter.capability_id == "test_capability_id"
    assert adapter.capability == capability


def test_capability_adapter_execute_success():
    """Test that CapabilityAdapter executes successfully."""
    from graph.tool_adapter import CapabilityAdapter
    
    class MockCapability:
        description = "Mock capability"
        parameters = {}
        
        def execute(self, **kwargs):
            return {"result": "success"}
    
    capability = MockCapability()
    adapter = CapabilityAdapter("test_capability", "test_capability_id", capability)
    
    result = adapter.execute(param="value")
    
    assert result["success"] is True
    assert result["result"] == {"result": "success"}
    assert result["tool_id"] == "test_capability"


def test_capability_adapter_get_schema():
    """Test that CapabilityAdapter returns correct schema."""
    from graph.tool_adapter import CapabilityAdapter
    
    class MockCapability:
        description = "Mock capability description"
        parameters = {"param1": {"type": "string"}}
        
        def execute(self, **kwargs):
            return {"result": "success"}
    
    capability = MockCapability()
    adapter = CapabilityAdapter("test_capability", "test_capability_id", capability)
    
    schema = adapter.get_schema()
    
    assert schema["name"] == "test_capability"
    assert schema["description"] == "Mock capability description"
    assert schema["parameters"] == {"param1": {"type": "string"}}


# ─── TOOL ADAPTER REGISTRY TESTS ────────────────────────────────────────────

def test_tool_adapter_registry_initialization():
    """Test that ToolAdapterRegistry can be initialized."""
    from graph.tool_adapter import ToolAdapterRegistry
    
    registry = ToolAdapterRegistry()
    
    assert registry is not None
    assert len(registry.adapters) == 0


def test_tool_adapter_registry_register_adapter():
    """Test that adapters can be registered."""
    from graph.tool_adapter import ToolAdapterRegistry, OperonixToolAdapter
    
    class MockAdapter(OperonixToolAdapter):
        def execute(self, **kwargs):
            return {"success": True}
        
        def get_schema(self):
            return {"name": "test", "description": "test", "parameters": {}}
    
    registry = ToolAdapterRegistry()
    adapter = MockAdapter("test_tool", "test_capability")
    
    registry.register_adapter(adapter)
    
    assert "test_tool" in registry.adapters
    assert registry.adapters["test_tool"] == adapter


def test_tool_adapter_registry_get_adapter():
    """Test that adapters can be retrieved."""
    from graph.tool_adapter import ToolAdapterRegistry, OperonixToolAdapter
    
    class MockAdapter(OperonixToolAdapter):
        def execute(self, **kwargs):
            return {"success": True}
        
        def get_schema(self):
            return {"name": "test", "description": "test", "parameters": {}}
    
    registry = ToolAdapterRegistry()
    adapter = MockAdapter("test_tool", "test_capability")
    registry.register_adapter(adapter)
    
    retrieved = registry.get_adapter("test_tool")
    
    assert retrieved == adapter


def test_tool_adapter_registry_get_adapter_not_found():
    """Test that get_adapter returns None for non-existent adapter."""
    from graph.tool_adapter import ToolAdapterRegistry
    
    registry = ToolAdapterRegistry()
    
    retrieved = registry.get_adapter("non_existent")
    
    assert retrieved is None


def test_tool_adapter_registry_list_adapters():
    """Test that list_adapters returns all adapter IDs."""
    from graph.tool_adapter import ToolAdapterRegistry, OperonixToolAdapter
    
    class MockAdapter(OperonixToolAdapter):
        def execute(self, **kwargs):
            return {"success": True}
        
        def get_schema(self):
            return {"name": "test", "description": "test", "parameters": {}}
    
    registry = ToolAdapterRegistry()
    
    adapter1 = MockAdapter("tool1", "capability1")
    adapter2 = MockAdapter("tool2", "capability2")
    
    registry.register_adapter(adapter1)
    registry.register_adapter(adapter2)
    
    tool_ids = registry.list_adapters()
    
    assert "tool1" in tool_ids
    assert "tool2" in tool_ids
    assert len(tool_ids) == 2


def test_tool_adapter_registry_get_all_schemas():
    """Test that get_all_schemas returns schemas for all adapters."""
    from graph.tool_adapter import ToolAdapterRegistry, OperonixToolAdapter
    
    class MockAdapter(OperonixToolAdapter):
        def execute(self, **kwargs):
            return {"success": True}
        
        def get_schema(self):
            return {"name": self.tool_id, "description": "test", "parameters": {}}
    
    registry = ToolAdapterRegistry()
    
    adapter1 = MockAdapter("tool1", "capability1")
    adapter2 = MockAdapter("tool2", "capability2")
    
    registry.register_adapter(adapter1)
    registry.register_adapter(adapter2)
    
    schemas = registry.get_all_schemas()
    
    assert "tool1" in schemas
    assert "tool2" in schemas
    assert schemas["tool1"]["name"] == "tool1"
    assert schemas["tool2"]["name"] == "tool2"


# ─── LANGCHAIN TOOL TESTS ───────────────────────────────────────────────────

def test_operonix_langchain_tool_initialization():
    """Test that OperonixLangChainTool can be initialized."""
    from graph.langchain_tools import OperonixLangChainTool
    from graph.tool_adapter import OperonixToolAdapter
    
    class MockAdapter(OperonixToolAdapter):
        def execute(self, **kwargs):
            return {"success": True, "result": "test"}
        
        def get_schema(self):
            return {"name": "test_tool", "description": "Test tool", "parameters": {}}
    
    adapter = MockAdapter("test_tool", "test_capability")
    tool = OperonixLangChainTool(adapter)
    
    assert tool.name == "test_tool"
    assert tool.description == "Test tool"
    assert tool.parameters == {}


def test_operonix_langchain_tool_run_success():
    """Test that OperonixLangChainTool runs successfully."""
    from graph.langchain_tools import OperonixLangChainTool
    from graph.tool_adapter import OperonixToolAdapter
    
    class MockAdapter(OperonixToolAdapter):
        def execute(self, **kwargs):
            return {"success": True, "result": "test result"}
        
        def get_schema(self):
            return {"name": "test_tool", "description": "Test tool", "parameters": {}}
    
    adapter = MockAdapter("test_tool", "test_capability")
    tool = OperonixLangChainTool(adapter)
    
    result = tool.run(param="value")
    
    assert result == "test result"


def test_operonix_langchain_tool_run_error():
    """Test that OperonixLangChainTool handles errors."""
    from graph.langchain_tools import OperonixLangChainTool
    from graph.tool_adapter import OperonixToolAdapter
    
    class MockAdapter(OperonixToolAdapter):
        def execute(self, **kwargs):
            return {"success": False, "error": "Test error"}
        
        def get_schema(self):
            return {"name": "test_tool", "description": "Test tool", "parameters": {}}
    
    adapter = MockAdapter("test_tool", "test_capability")
    tool = OperonixLangChainTool(adapter)
    
    result = tool.run(param="value")
    
    assert result == "Error: Test error"


def test_operonix_langchain_tool_arun():
    """Test that OperonixLangChainTool arun works."""
    from graph.langchain_tools import OperonixLangChainTool
    from graph.tool_adapter import OperonixToolAdapter
    
    class MockAdapter(OperonixToolAdapter):
        def execute(self, **kwargs):
            return {"success": True, "result": "test result"}
        
        def get_schema(self):
            return {"name": "test_tool", "description": "Test tool", "parameters": {}}
    
    adapter = MockAdapter("test_tool", "test_capability")
    tool = OperonixLangChainTool(adapter)
    
    # For now, arun calls run synchronously
    result = tool.arun(param="value")
    
    assert result == "test result"


# ─── LANGCHAIN TOOL FACTORY TESTS ───────────────────────────────────────────

def test_langchain_tool_factory_initialization():
    """Test that LangChainToolFactory can be initialized."""
    from graph.langchain_tools import LangChainToolFactory
    
    factory = LangChainToolFactory()
    
    assert factory is not None


def test_langchain_tool_factory_create_tool():
    """Test that LangChainToolFactory can create a tool."""
    from graph.langchain_tools import LangChainToolFactory
    from graph.tool_adapter import OperonixToolAdapter
    
    class MockAdapter(OperonixToolAdapter):
        def execute(self, **kwargs):
            return {"success": True, "result": "test"}
        
        def get_schema(self):
            return {"name": "test_tool", "description": "Test tool", "parameters": {}}
    
    factory = LangChainToolFactory()
    adapter = MockAdapter("test_tool", "test_capability")
    
    tool = factory.create_tool(adapter)
    
    assert tool.name == "test_tool"


def test_langchain_tool_factory_create_tools_from_registry():
    """Test that LangChainToolFactory can create tools from registry."""
    from graph.langchain_tools import LangChainToolFactory
    from graph.tool_adapter import ToolAdapterRegistry, OperonixToolAdapter
    
    class MockAdapter(OperonixToolAdapter):
        def execute(self, **kwargs):
            return {"success": True, "result": "test"}
        
        def get_schema(self):
            return {"name": self.tool_id, "description": "Test tool", "parameters": {}}
    
    registry = ToolAdapterRegistry()
    
    adapter1 = MockAdapter("tool1", "capability1")
    adapter2 = MockAdapter("tool2", "capability2")
    
    registry.register_adapter(adapter1)
    registry.register_adapter(adapter2)
    
    factory = LangChainToolFactory()
    tools = factory.create_tools_from_registry(registry)
    
    assert len(tools) == 2
    assert tools[0].name == "tool1"
    assert tools[1].name == "tool2"


def test_langchain_tool_factory_create_tool_list():
    """Test that LangChainToolFactory can create tools from list."""
    from graph.langchain_tools import LangChainToolFactory
    from graph.tool_adapter import OperonixToolAdapter
    
    class MockAdapter(OperonixToolAdapter):
        def execute(self, **kwargs):
            return {"success": True, "result": "test"}
        
        def get_schema(self):
            return {"name": self.tool_id, "description": "Test tool", "parameters": {}}
    
    adapter1 = MockAdapter("tool1", "capability1")
    adapter2 = MockAdapter("tool2", "capability2")
    
    factory = LangChainToolFactory()
    tools = factory.create_tool_list([adapter1, adapter2])
    
    assert len(tools) == 2
    assert tools[0].name == "tool1"
    assert tools[1].name == "tool2"


# ─── GLOBAL INSTANCES TESTS ───────────────────────────────────────────────

def test_get_tool_adapter_registry():
    """Test that get_tool_adapter_registry returns singleton."""
    from graph.tool_adapter import get_tool_adapter_registry
    
    registry1 = get_tool_adapter_registry()
    registry2 = get_tool_adapter_registry()
    
    assert registry1 is registry2


def test_get_langchain_tool_factory():
    """Test that get_langchain_tool_factory returns singleton."""
    from graph.langchain_tools import get_langchain_tool_factory
    
    factory1 = get_langchain_tool_factory()
    factory2 = get_langchain_tool_factory()
    
    assert factory1 is factory2


def test_get_langchain_tools():
    """Test that get_langchain_tools returns tools from registry."""
    from graph.langchain_tools import get_langchain_tools
    from graph.tool_adapter import ToolAdapterRegistry, get_tool_adapter_registry, OperonixToolAdapter
    
    class MockAdapter(OperonixToolAdapter):
        def execute(self, **kwargs):
            return {"success": True, "result": "test"}
        
        def get_schema(self):
            return {"name": self.tool_id, "description": "Test tool", "parameters": {}}
    
    registry = get_tool_adapter_registry()
    
    adapter = MockAdapter("test_tool", "test_capability")
    registry.register_adapter(adapter)
    
    tools = get_langchain_tools()
    
    assert len(tools) == 1
    assert tools[0].name == "test_tool"
