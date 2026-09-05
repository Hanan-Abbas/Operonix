"""
Graph Foundation Tests — Operonix Phase 1
─────────────────────────────────────────

Tests for Phase 1 graph foundation implementation.
These tests verify that:
- Graph can be built
- State flows through nodes
- Runtime adapter works
- Feature flags control graph usage
"""
from __future__ import annotations

import pytest
from typing import Dict, Any


# ─── GRAPH TOPOLOGY TESTS ─────────────────────────────────────────────────────

def test_graph_can_be_imported():
    """Test that graph module can be imported."""
    try:
        import graph
        assert graph is not None
    except ImportError:
        pytest.skip("LangGraph not installed")


def test_graph_builder_exists():
    """Test that graph builder function exists."""
    try:
        from graph.graph import build_operonix_graph
        assert build_operonix_graph is not None
    except ImportError:
        pytest.skip("LangGraph not installed")


def test_graph_runner_exists():
    """Test that graph runner class exists."""
    try:
        from graph.graph import OperonixGraphRunner
        assert OperonixGraphRunner is not None
    except ImportError:
        pytest.skip("LangGraph not installed")


def test_graph_runner_instance_exists():
    """Test that global graph runner instance exists."""
    try:
        from graph.graph import graph_runner
        assert graph_runner is not None
    except ImportError:
        pytest.skip("LangGraph not installed")


# ─── NODE TESTS ───────────────────────────────────────────────────────────────

def test_intake_node_exists():
    """Test that intake node can be imported."""
    from graph.nodes.intake import intake_node
    assert intake_node is not None


def test_observe_node_exists():
    """Test that observe node can be imported."""
    from graph.nodes.observe import observe_node
    assert observe_node is not None


def test_finalize_node_exists():
    """Test that finalize node can be imported."""
    from graph.nodes.finalize import finalize_node
    assert finalize_node is not None


def test_intake_node_with_state():
    """Test that intake node processes state correctly."""
    from graph.nodes.intake import intake_node
    from migration.graph_state import OperonixState
    from migration.domain_contracts import TaskRequest, TaskSource
    
    task = TaskRequest(user_input="test", source=TaskSource.API)
    state = OperonixState(task=task)
    
    result = intake_node(state)
    
    assert "state" in result
    assert result["state"] == state
    assert len(state.history.get("events", [])) > 0


def test_observe_node_with_state():
    """Test that observe node processes state correctly."""
    from graph.nodes.observe import observe_node
    from migration.graph_state import OperonixState
    from migration.domain_contracts import TaskRequest, TaskSource
    
    task = TaskRequest(user_input="test", source=TaskSource.API)
    state = OperonixState(task=task)
    
    result = observe_node(state)
    
    assert "state" in result
    assert result["state"] == state
    assert len(state.history.get("events", [])) > 0


def test_finalize_node_with_state():
    """Test that finalize node produces final result."""
    from graph.nodes.finalize import finalize_node
    from migration.graph_state import OperonixState
    from migration.domain_contracts import TaskRequest, TaskSource
    
    task = TaskRequest(user_input="test", source=TaskSource.API)
    state = OperonixState(task=task)
    
    result = finalize_node(state)
    
    assert "state" in result
    assert result["state"].final is not None
    assert result["state"].final.success is True
    assert result["state"].final.task_id == task.task_id


# ─── RUNTIME ADAPTER TESTS ────────────────────────────────────────────────────

def test_runtime_adapter_exists():
    """Test that runtime adapter can be imported."""
    from graph.runtime_adapter import RuntimeGraphAdapter
    assert RuntimeGraphAdapter is not None


def test_runtime_adapter_instance_exists():
    """Test that global runtime adapter instance exists."""
    from graph.runtime_adapter import runtime_adapter
    assert runtime_adapter is not None


def test_runtime_adapter_can_create_task_request():
    """Test that runtime adapter can create task requests."""
    from graph.runtime_adapter import runtime_adapter
    from migration.domain_contracts import TaskSource
    
    request = runtime_adapter.create_task_request(
        user_input="Open Firefox",
        source=TaskSource.VOICE
    )
    
    assert request.task_id is not None
    assert request.user_input == "Open Firefox"
    assert request.source == TaskSource.VOICE


def test_runtime_adapter_get_graph_status():
    """Test that runtime adapter can report graph status."""
    from graph.runtime_adapter import runtime_adapter
    
    status = runtime_adapter.get_graph_status()
    
    assert isinstance(status, dict)
    assert "graph_enabled" in status
    assert "graph_available" in status
    assert "migration_phase" in status
    assert "all_flags" in status


def test_runtime_adapter_graph_disabled_by_default():
    """Test that graph is disabled by default (safe default)."""
    from graph.runtime_adapter import runtime_adapter
    
    # Graph should be disabled by default
    assert runtime_adapter.is_graph_enabled() is False


# ─── INTEGRATION TESTS ───────────────────────────────────────────────────────

def test_node_sequence():
    """Test that nodes can be called in sequence."""
    from graph.nodes.intake import intake_node
    from graph.nodes.observe import observe_node
    from graph.nodes.finalize import finalize_node
    from migration.graph_state import OperonixState
    from migration.domain_contracts import TaskRequest, TaskSource
    
    task = TaskRequest(user_input="Open Firefox", source=TaskSource.VOICE)
    state = OperonixState(task=task)
    
    # Execute node sequence
    state = intake_node(state)["state"]
    state = observe_node(state)["state"]
    state = finalize_node(state)["state"]
    
    # Verify final state
    assert state.final is not None
    assert state.final.success is True
    assert len(state.history.get("events", [])) >= 3


def test_state_history_tracking():
    """Test that state properly tracks history through nodes."""
    from graph.nodes.intake import intake_node
    from graph.nodes.observe import observe_node
    from migration.graph_state import OperonixState
    from migration.domain_contracts import TaskRequest, TaskSource
    
    task = TaskRequest(user_input="test", source=TaskSource.API)
    state = OperonixState(task=task)
    
    intake_node(state)
    observe_node(state)
    
    events = state.history.get("events", [])
    assert len(events) >= 4  # 2 nodes x 2 events each (started/completed)
    
    event_types = [e["type"] for e in events]
    assert "intake_started" in event_types
    assert "intake_completed" in event_types
    assert "observe_started" in event_types
    assert "observe_completed" in event_types


def test_state_timestamp_updates():
    """Test that state updates timestamp on modifications."""
    from graph.nodes.intake import intake_node
    from migration.graph_state import OperonixState
    from migration.domain_contracts import TaskRequest, TaskSource
    from datetime import datetime
    
    task = TaskRequest(user_input="test", source=TaskSource.API)
    state = OperonixState(task=task)
    
    initial_timestamp = state.updated_at
    
    # Wait a tiny bit to ensure timestamp difference
    import time
    time.sleep(0.001)
    
    intake_node(state)
    
    # Timestamp should have been updated
    assert state.updated_at > initial_timestamp


# ─── PACKAGE STRUCTURE TESTS ─────────────────────────────────────────────────

def test_graph_package_structure():
    """Test that graph package has correct structure."""
    import graph
    from graph import graph
    from graph import nodes
    from graph import runtime_adapter
    
    assert graph is not None
    assert nodes is not None
    assert runtime_adapter is not None


def test_nodes_package_structure():
    """Test that nodes package has correct structure."""
    from graph.nodes import intake
    from graph.nodes import observe
    from graph.nodes import finalize
    
    assert intake is not None
    assert observe is not None
    assert finalize is not None


if __name__ == "__main__":
    pytest.main([__file__, "-v"])
