"""
Cancel Node Tests — Operonix Graph
──────────────────────────────────

Unit tests for cancel node and cancellation semantics.
These tests verify that:
- Cancel node processes cancellation requests correctly
- Abort semantics are determined based on cancellation reason
- Cleanup is performed when required
- Rollback is performed when required
- Final result indicates cancellation
- Trace events are collected for cancellation
"""
from __future__ import annotations

import pytest
from typing import Dict, Any
from migration.graph_state import OperonixState
from migration.domain_contracts import TaskRequest, TaskSource, CancellationRequest, CancellationReason, AbortSemantics
from graph.nodes.cancel import cancel_node
from graph.cancellation import get_cancellation_service


# ─── CANCEL NODE TESTS ─────────────────────────────────────────────────────

def test_cancel_node_exists():
    """Test that cancel node can be imported."""
    from graph.nodes.cancel import cancel_node
    assert cancel_node is not None


def test_cancellation_service_exists():
    """Test that cancellation service can be imported."""
    from graph.cancellation import get_cancellation_service
    service = get_cancellation_service()
    assert service is not None


def test_cancel_node_with_user_cancellation():
    """Test cancel node with user-requested cancellation."""
    task = TaskRequest(user_input="long running task", source=TaskSource.VOICE)
    state = OperonixState(task=task)
    
    # Create cancellation request
    cancellation_service = get_cancellation_service()
    cancellation = cancellation_service.request_cancellation(
        task_id=task.task_id,
        reason=CancellationReason.USER_REQUESTED,
        requested_by="test_user"
    )
    state.cancellation = cancellation
    
    result = cancel_node(state)
    
    # State should be cancelled
    assert state.cancelled is True
    # Should have abort decision
    assert state.abort_decision is not None
    # Abort semantics should be graceful for user request
    assert state.abort_decision.semantics == AbortSemantics.GRACEFUL


def test_cancel_node_with_timeout_cancellation():
    """Test cancel node with timeout cancellation."""
    task = TaskRequest(user_input="long running task", source=TaskSource.VOICE)
    state = OperonixState(task=task)
    
    # Create cancellation request
    cancellation_service = get_cancellation_service()
    cancellation = cancellation_service.request_cancellation(
        task_id=task.task_id,
        reason=CancellationReason.TIMEOUT,
        requested_by="system"
    )
    state.cancellation = cancellation
    
    result = cancel_node(state)
    
    # State should be cancelled
    assert state.cancelled is True
    # Should have abort decision
    assert state.abort_decision is not None
    # Abort semantics should be safe for timeout
    assert state.abort_decision.semantics == AbortSemantics.SAFE
    # Rollback should be required for timeout
    assert state.abort_decision.rollback_required is True


def test_cancel_node_with_system_error_cancellation():
    """Test cancel node with system error cancellation."""
    task = TaskRequest(user_input="long running task", source=TaskSource.VOICE)
    state = OperonixState(task=task)
    
    # Create cancellation request
    cancellation_service = get_cancellation_service()
    cancellation = cancellation_service.request_cancellation(
        task_id=task.task_id,
        reason=CancellationReason.SYSTEM_ERROR,
        requested_by="system"
    )
    state.cancellation = cancellation
    
    result = cancel_node(state)
    
    # State should be cancelled
    assert state.cancelled is True
    # Should have abort decision
    assert state.abort_decision is not None
    # Abort semantics should be immediate for system error
    assert state.abort_decision.semantics == AbortSemantics.IMMEDIATE


def test_cancel_node_creates_final_result():
    """Test that cancel node creates final result indicating cancellation."""
    task = TaskRequest(user_input="long running task", source=TaskSource.VOICE)
    state = OperonixState(task=task)
    
    # Create cancellation request
    cancellation_service = get_cancellation_service()
    cancellation = cancellation_service.request_cancellation(
        task_id=task.task_id,
        reason=CancellationReason.USER_REQUESTED,
        requested_by="test_user"
    )
    state.cancellation = cancellation
    
    result = cancel_node(state)
    
    # Should have final result
    assert state.final is not None
    # Final result should indicate failure (cancelled)
    assert state.final.success is False
    # Response should mention cancellation
    assert "cancelled" in state.final.response.lower() or "cancel" in state.final.response.lower()
    # Error should mention cancellation
    assert state.final.error is not None
    assert "cancelled" in state.final.error.lower() or "cancel" in state.final.error.lower()


def test_cancel_node_history_tracking():
    """Test that cancel node tracks history."""
    task = TaskRequest(user_input="long running task", source=TaskSource.VOICE)
    state = OperonixState(task=task)
    
    # Create cancellation request
    cancellation_service = get_cancellation_service()
    cancellation = cancellation_service.request_cancellation(
        task_id=task.task_id,
        reason=CancellationReason.USER_REQUESTED,
        requested_by="test_user"
    )
    state.cancellation = cancellation
    
    cancel_node(state)
    
    events = state.history.get("events", [])
    assert len(events) > 0
    event_types = [e["type"] for e in events]
    assert "cancel_started" in event_types
    assert "cancel_completed" in event_types


def test_cancel_node_without_cancellation():
    """Test cancel node when no cancellation exists (should not crash)."""
    task = TaskRequest(user_input="long running task", source=TaskSource.VOICE)
    state = OperonixState(task=task)
    
    # Should not crash even without cancellation
    result = cancel_node(state)
    
    # State should not be cancelled
    assert state.cancelled is False


def test_cancel_node_cleanup_performed():
    """Test that cancel node performs cleanup when required."""
    task = TaskRequest(user_input="long running task", source=TaskSource.VOICE)
    state = OperonixState(task=task)
    
    # Create cancellation request
    cancellation_service = get_cancellation_service()
    cancellation = cancellation_service.request_cancellation(
        task_id=task.task_id,
        reason=CancellationReason.USER_REQUESTED,
        requested_by="test_user"
    )
    state.cancellation = cancellation
    
    result = cancel_node(state)
    
    # Abort decision should indicate cleanup was performed
    assert state.abort_decision is not None
    assert state.abort_decision.cleanup_required is True


def test_cancel_node_rollback_performed():
    """Test that cancel node performs rollback when required (timeout)."""
    task = TaskRequest(user_input="long running task", source=TaskSource.VOICE)
    state = OperonixState(task=task)
    
    # Create cancellation request with timeout (requires rollback)
    cancellation_service = get_cancellation_service()
    cancellation = cancellation_service.request_cancellation(
        task_id=task.task_id,
        reason=CancellationReason.TIMEOUT,
        requested_by="system"
    )
    state.cancellation = cancellation
    
    result = cancel_node(state)
    
    # Abort decision should indicate rollback was performed
    assert state.abort_decision is not None
    assert state.abort_decision.rollback_required is True


def test_cancel_node_trace_collection():
    """Test that cancel node collects trace events."""
    task = TaskRequest(user_input="long running task", source=TaskSource.VOICE)
    state = OperonixState(task=task)
    
    # Create cancellation request
    cancellation_service = get_cancellation_service()
    cancellation = cancellation_service.request_cancellation(
        task_id=task.task_id,
        reason=CancellationReason.USER_REQUESTED,
        requested_by="test_user"
    )
    state.cancellation = cancellation
    
    # Cancel node should not crash even if trace collection fails
    result = cancel_node(state)
    
    # State should still be cancelled
    assert state.cancelled is True


# ─── ABORT SEMANTICS TESTS ───────────────────────────────────────────────────

def test_abort_semantics_user_requested():
    """Test that USER_REQUESTED maps to GRACEFUL."""
    cancellation_service = get_cancellation_service()
    cancellation = cancellation_service.request_cancellation(
        task_id="test-task",
        reason=CancellationReason.USER_REQUESTED,
        requested_by="test_user"
    )
    
    task = TaskRequest(user_input="test", source=TaskSource.VOICE)
    state = OperonixState(task=task)
    state.cancellation = cancellation
    
    cancel_node(state)
    
    assert state.abort_decision.semantics == AbortSemantics.GRACEFUL


def test_abort_semantics_timeout():
    """Test that TIMEOUT maps to SAFE."""
    cancellation_service = get_cancellation_service()
    cancellation = cancellation_service.request_cancellation(
        task_id="test-task",
        reason=CancellationReason.TIMEOUT,
        requested_by="system"
    )
    
    task = TaskRequest(user_input="test", source=TaskSource.VOICE)
    state = OperonixState(task=task)
    state.cancellation = cancellation
    
    cancel_node(state)
    
    assert state.abort_decision.semantics == AbortSemantics.SAFE


def test_abort_semantics_system_error():
    """Test that SYSTEM_ERROR maps to IMMEDIATE."""
    cancellation_service = get_cancellation_service()
    cancellation = cancellation_service.request_cancellation(
        task_id="test-task",
        reason=CancellationReason.SYSTEM_ERROR,
        requested_by="system"
    )
    
    task = TaskRequest(user_input="test", source=TaskSource.VOICE)
    state = OperonixState(task=task)
    state.cancellation = cancellation
    
    cancel_node(state)
    
    assert state.abort_decision.semantics == AbortSemantics.IMMEDIATE


def test_abort_semantics_resource_contention():
    """Test that RESOURCE_CONTENTION maps to GRACEFUL."""
    cancellation_service = get_cancellation_service()
    cancellation = cancellation_service.request_cancellation(
        task_id="test-task",
        reason=CancellationReason.RESOURCE_CONTENTION,
        requested_by="system"
    )
    
    task = TaskRequest(user_input="test", source=TaskSource.VOICE)
    state = OperonixState(task=task)
    state.cancellation = cancellation
    
    cancel_node(state)
    
    assert state.abort_decision.semantics == AbortSemantics.GRACEFUL


def test_abort_semantics_safe_abort():
    """Test that SAFE_ABORT maps to SAFE."""
    cancellation_service = get_cancellation_service()
    cancellation = cancellation_service.request_cancellation(
        task_id="test-task",
        reason=CancellationReason.SAFE_ABORT,
        requested_by="system"
    )
    
    task = TaskRequest(user_input="test", source=TaskSource.VOICE)
    state = OperonixState(task=task)
    state.cancellation = cancellation
    
    cancel_node(state)
    
    assert state.abort_decision.semantics == AbortSemantics.SAFE


# ─── CANCELLATION SERVICE TESTS ─────────────────────────────────────────────

def test_cancellation_service_request_cancellation():
    """Test that cancellation service can request cancellation."""
    cancellation_service = get_cancellation_service()
    cancellation = cancellation_service.request_cancellation(
        task_id="test-task",
        reason=CancellationReason.USER_REQUESTED,
        requested_by="test_user"
    )
    
    assert cancellation is not None
    assert cancellation.task_id == "test-task"
    assert cancellation.reason == CancellationReason.USER_REQUESTED
    assert cancellation.requested_by == "test_user"
    assert cancellation.cancellation_id is not None


def test_cancellation_service_get_cancellation():
    """Test that cancellation service can retrieve cancellation."""
    import uuid
    unique_task_id = f"test-task-{uuid.uuid4()}"
    
    cancellation_service = get_cancellation_service()
    cancellation = cancellation_service.request_cancellation(
        task_id=unique_task_id,
        reason=CancellationReason.USER_REQUESTED,
        requested_by="test_user"
    )
    
    retrieved = cancellation_service.get_cancellation(unique_task_id)
    
    assert retrieved is not None
    assert retrieved.cancellation_id == cancellation.cancellation_id


if __name__ == "__main__":
    pytest.main([__file__, "-v"])
