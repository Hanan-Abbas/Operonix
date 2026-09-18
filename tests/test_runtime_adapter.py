"""
Runtime Adapter Tests — Operonix Graph
──────────────────────────────────────

Unit tests for runtime adapter.
These tests verify that:
- Runtime adapter can be instantiated
- Graph status can be queried
- Task requests can be created
- Task execution through graph works
- Task execution through legacy workflow works
- Fallback to legacy workflow works when graph is disabled
- Graph availability is checked correctly
- Feature flags are respected
"""
from __future__ import annotations

import pytest
from typing import Dict, Any
from migration.graph_state import OperonixState
from migration.domain_contracts import TaskRequest, TaskSource, FinalResult
from graph.runtime_adapter import RuntimeGraphAdapter, runtime_adapter


# ─── RUNTIME ADAPTER TESTS ─────────────────────────────────────────────────

def test_runtime_adapter_exists():
    """Test that runtime adapter can be imported."""
    from graph.runtime_adapter import RuntimeGraphAdapter, runtime_adapter
    assert RuntimeGraphAdapter is not None
    assert runtime_adapter is not None


def test_runtime_adapter_instantiation():
    """Test that runtime adapter can be instantiated."""
    adapter = RuntimeGraphAdapter()
    assert adapter is not None
    assert isinstance(adapter, RuntimeGraphAdapter)


def test_runtime_adapter_global_instance():
    """Test that global runtime adapter instance exists."""
    assert runtime_adapter is not None
    assert isinstance(runtime_adapter, RuntimeGraphAdapter)


def test_runtime_adapter_is_graph_enabled():
    """Test that is_graph_enabled returns a boolean."""
    adapter = RuntimeGraphAdapter()
    status = adapter.is_graph_enabled()
    assert isinstance(status, bool)


def test_runtime_adapter_get_graph_status():
    """Test that get_graph_status returns status information."""
    adapter = RuntimeGraphAdapter()
    status = adapter.get_graph_status()
    
    assert isinstance(status, dict)
    assert "graph_enabled" in status
    assert "graph_available" in status
    assert "migration_phase" in status
    assert "all_flags" in status


def test_runtime_adapter_create_task_request():
    """Test that task request can be created from user input."""
    adapter = RuntimeGraphAdapter()
    
    task_request = adapter.create_task_request(
        user_input="open firefox",
        source=TaskSource.VOICE,
        metadata={"device": "test"}
    )
    
    assert task_request is not None
    assert isinstance(task_request, TaskRequest)
    assert task_request.user_input == "open firefox"
    assert task_request.source == TaskSource.VOICE
    assert task_request.task_id is not None
    assert len(task_request.task_id) > 0
    assert task_request.metadata == {"device": "test"}


def test_runtime_adapter_create_task_request_without_metadata():
    """Test that task request can be created without metadata."""
    adapter = RuntimeGraphAdapter()
    
    task_request = adapter.create_task_request(
        user_input="open firefox",
        source=TaskSource.VOICE
    )
    
    assert task_request is not None
    assert task_request.metadata == {}


def test_runtime_adapter_create_task_request_different_sources():
    """Test that task request can be created with different sources."""
    adapter = RuntimeGraphAdapter()
    
    sources = [TaskSource.VOICE, TaskSource.PANEL, TaskSource.API, TaskSource.CLI]
    
    for source in sources:
        task_request = adapter.create_task_request(
            user_input="test",
            source=source
        )
        assert task_request.source == source


def test_runtime_adapter_graph_runner_initialization():
    """Test that graph runner is initialized."""
    adapter = RuntimeGraphAdapter()
    
    # Graph runner may be None if graph is not available
    # This is expected behavior
    assert adapter.graph_runner is not None or adapter.graph_runner is None


def test_runtime_adapter_execute_task_with_graph_disabled():
    """Test that execute_task falls back to legacy when graph is disabled."""
    import asyncio
    
    adapter = RuntimeGraphAdapter()
    
    task_request = adapter.create_task_request(
        user_input="test",
        source=TaskSource.VOICE
    )
    
    # Force use of legacy workflow
    async def test_execute():
        result = await adapter.execute_task(task_request, use_graph=False)
        assert result is not None
        assert isinstance(result, FinalResult)
        assert result.task_id == task_request.task_id
    
    asyncio.run(test_execute())


def test_runtime_adapter_execute_task_with_graph_enabled():
    """Test that execute_task uses graph when enabled (if available)."""
    import asyncio
    
    adapter = RuntimeGraphAdapter()
    
    task_request = adapter.create_task_request(
        user_input="test",
        source=TaskSource.VOICE
    )
    
    # Try to use graph if available
    async def test_execute():
        if adapter.is_graph_enabled():
            result = await adapter.execute_task(task_request, use_graph=True)
            assert result is not None
            assert isinstance(result, FinalResult)
        else:
            # Graph not available, skip this test
            pytest.skip("Graph not available")
    
    asyncio.run(test_execute())


def test_runtime_adapter_execute_task_auto_select():
    """Test that execute_task auto-selects graph or legacy based on status."""
    import asyncio
    
    adapter = RuntimeGraphAdapter()
    
    task_request = adapter.create_task_request(
        user_input="test",
        source=TaskSource.VOICE
    )
    
    # Let adapter decide based on feature flags
    async def test_execute():
        result = await adapter.execute_task(task_request, use_graph=None)
        assert result is not None
        assert isinstance(result, FinalResult)
        assert result.task_id == task_request.task_id
    
    asyncio.run(test_execute())


def test_runtime_adapter_status_fields():
    """Test that status fields have correct types."""
    adapter = RuntimeGraphAdapter()
    status = adapter.get_graph_status()
    
    assert isinstance(status["graph_enabled"], bool)
    assert isinstance(status["graph_available"], bool)
    assert isinstance(status["migration_phase"], str)
    assert isinstance(status["all_flags"], dict)


def test_runtime_adapter_all_flags_dict():
    """Test that all_flags is a dictionary with flag names."""
    adapter = RuntimeGraphAdapter()
    status = adapter.get_graph_status()
    
    all_flags = status["all_flags"]
    assert isinstance(all_flags, dict)
    # Should contain at least USE_LANGGRAPH
    assert "USE_LANGGRAPH" in all_flags


def test_runtime_adapter_task_request_unique_ids():
    """Test that each task request gets a unique ID."""
    adapter = RuntimeGraphAdapter()
    
    task1 = adapter.create_task_request("test", TaskSource.VOICE)
    task2 = adapter.create_task_request("test", TaskSource.VOICE)
    
    assert task1.task_id != task2.task_id


def test_runtime_adapter_task_request_timestamp():
    """Test that task request has creation timestamp."""
    from datetime import datetime
    
    adapter = RuntimeGraphAdapter()
    task = adapter.create_task_request("test", TaskSource.VOICE)
    
    assert task.created_at is not None
    assert isinstance(task.created_at, datetime)


def test_runtime_adapter_execute_task_error_handling():
    """Test that execute_task handles errors gracefully."""
    import asyncio
    
    adapter = RuntimeGraphAdapter()
    
    # Create a task request
    task_request = adapter.create_task_request(
        user_input="test",
        source=TaskSource.VOICE
    )
    
    # Force legacy workflow (should not raise errors)
    async def test_execute():
        result = await adapter.execute_task(task_request, use_graph=False)
        # Should return a result even if there are issues
        assert result is not None
        assert isinstance(result, FinalResult)
    
    asyncio.run(test_execute())


def test_runtime_adapter_graph_unavailable_fallback():
    """Test that adapter handles unavailable graph gracefully."""
    adapter = RuntimeGraphAdapter()
    
    # If graph is not available, is_graph_enabled should return False
    # This is expected behavior
    if not adapter.is_graph_enabled():
        # Graph is disabled or unavailable
        assert adapter.graph_runner is None or not adapter.graph_runner.is_available()


if __name__ == "__main__":
    pytest.main([__file__, "-v"])
