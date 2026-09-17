"""
Confirmation Node Tests — Operonix Graph
───────────────────────────────────────

Unit tests for confirmation node and pause/resume flow.
These tests verify that:
- Confirmation node creates human intervention requests
- Checkpoint is created before pausing
- State is marked as paused
- Resume mechanism works correctly
- Human response is applied to state
- Checkpoint identifier is returned
"""
from __future__ import annotations

import pytest
from typing import Dict, Any
from migration.graph_state import OperonixState
from migration.domain_contracts import TaskRequest, TaskSource, HumanIntervention, HumanInterventionType
from graph.nodes.confirmation import confirmation_node, resume_from_confirmation


# ─── CONFIRMATION NODE TESTS ─────────────────────────────────────────────────

def test_confirmation_node_exists():
    """Test that confirmation node can be imported."""
    from graph.nodes.confirmation import confirmation_node
    assert confirmation_node is not None


def test_resume_from_confirmation_exists():
    """Test that resume_from_confirmation function can be imported."""
    from graph.nodes.confirmation import resume_from_confirmation
    assert resume_from_confirmation is not None


def test_confirmation_node_creates_human_intervention():
    """Test that confirmation node creates human intervention request."""
    task = TaskRequest(user_input="delete file", source=TaskSource.VOICE)
    state = OperonixState(task=task)
    
    result = confirmation_node(state)
    
    # Should have confirmation object
    assert state.confirmation is not None
    assert isinstance(state.confirmation, HumanIntervention)
    assert state.confirmation.task_id == task.task_id


def test_confirmation_node_sets_paused():
    """Test that confirmation node sets paused state."""
    task = TaskRequest(user_input="delete file", source=TaskSource.VOICE)
    state = OperonixState(task=task)
    
    result = confirmation_node(state)
    
    # State should be paused
    assert state.paused is True


def test_confirmation_node_creates_checkpoint():
    """Test that confirmation node creates checkpoint."""
    task = TaskRequest(user_input="delete file", source=TaskSource.VOICE)
    state = OperonixState(task=task)
    
    result = confirmation_node(state)
    
    # Should have checkpoint identifier
    assert state.checkpoint_identifier is not None
    assert len(state.checkpoint_identifier) > 0


def test_confirmation_node_returns_checkpoint_identifier():
    """Test that confirmation node returns checkpoint identifier in result."""
    task = TaskRequest(user_input="delete file", source=TaskSource.VOICE)
    state = OperonixState(task=task)
    
    result = confirmation_node(state)
    
    # Result should include checkpoint_identifier
    assert "checkpoint_identifier" in result
    assert result["checkpoint_identifier"] is not None


def test_confirmation_node_returns_paused():
    """Test that confirmation node returns paused status in result."""
    task = TaskRequest(user_input="delete file", source=TaskSource.VOICE)
    state = OperonixState(task=task)
    
    result = confirmation_node(state)
    
    # Result should include paused status
    assert "paused" in result
    assert result["paused"] is True


def test_confirmation_node_history_tracking():
    """Test that confirmation node tracks history."""
    task = TaskRequest(user_input="delete file", source=TaskSource.VOICE)
    state = OperonixState(task=task)
    
    confirmation_node(state)
    
    events = state.history.get("events", [])
    assert len(events) > 0
    event_types = [e["type"] for e in events]
    assert "confirmation_started" in event_types
    assert "confirmation_paused" in event_types


def test_confirmation_node_intervention_type():
    """Test that confirmation node sets correct intervention type."""
    task = TaskRequest(user_input="delete file", source=TaskSource.VOICE)
    state = OperonixState(task=task)
    
    result = confirmation_node(state)
    
    # Should have CONFIRM intervention type
    assert state.confirmation.intervention_type == HumanInterventionType.CONFIRM


def test_confirmation_node_context():
    """Test that confirmation node includes context in intervention."""
    task = TaskRequest(user_input="delete file", source=TaskSource.VOICE)
    state = OperonixState(task=task)
    
    result = confirmation_node(state)
    
    # Should have context
    assert state.confirmation.context is not None
    assert isinstance(state.confirmation.context, dict)


# ─── RESUME FROM CONFIRMATION TESTS ───────────────────────────────────────────

def test_resume_from_confirmation_confirm():
    """Test resume with CONFIRM response."""
    task = TaskRequest(user_input="delete file", source=TaskSource.VOICE)
    state = OperonixState(task=task)
    state.confirmation = HumanIntervention(
        task_id=task.task_id,
        intervention_type=HumanInterventionType.CONFIRM,
        reason="Safety check requires confirmation"
    )
    state.paused = True
    
    result = resume_from_confirmation(state, HumanInterventionType.CONFIRM)
    
    # State should be resumed
    assert state.paused is False
    # Confirmation should have response
    assert state.confirmation.response == HumanInterventionType.CONFIRM
    # Should have responded_at timestamp
    assert state.confirmation.responded_at is not None


def test_resume_from_confirmation_deny():
    """Test resume with DENY response."""
    task = TaskRequest(user_input="delete file", source=TaskSource.VOICE)
    state = OperonixState(task=task)
    state.confirmation = HumanIntervention(
        task_id=task.task_id,
        intervention_type=HumanInterventionType.CONFIRM,
        reason="Safety check requires confirmation"
    )
    state.paused = True
    
    result = resume_from_confirmation(state, HumanInterventionType.DENY)
    
    # State should be resumed
    assert state.paused is False
    # Confirmation should have response
    assert state.confirmation.response == HumanInterventionType.DENY


def test_resume_from_confirmation_without_confirmation():
    """Test resume when no confirmation exists (should not crash)."""
    task = TaskRequest(user_input="delete file", source=TaskSource.VOICE)
    state = OperonixState(task=task)
    state.paused = True
    
    # Should not crash even without confirmation
    result = resume_from_confirmation(state, HumanInterventionType.CONFIRM)
    
    # State should still be resumed
    assert state.paused is False


def test_resume_from_confirmation_history_tracking():
    """Test that resume_from_confirmation tracks history."""
    task = TaskRequest(user_input="delete file", source=TaskSource.VOICE)
    state = OperonixState(task=task)
    state.confirmation = HumanIntervention(
        task_id=task.task_id,
        intervention_type=HumanInterventionType.CONFIRM,
        reason="Safety check requires confirmation"
    )
    state.paused = True
    
    resume_from_confirmation(state, HumanInterventionType.CONFIRM)
    
    events = state.history.get("events", [])
    assert len(events) > 0
    event_types = [e["type"] for e in events]
    assert "confirmation_resumed" in event_types


def test_resume_from_confirmation_returns_state_update():
    """Test that resume_from_confirmation returns state update."""
    task = TaskRequest(user_input="delete file", source=TaskSource.VOICE)
    state = OperonixState(task=task)
    state.confirmation = HumanIntervention(
        task_id=task.task_id,
        intervention_type=HumanInterventionType.CONFIRM,
        reason="Safety check requires confirmation"
    )
    state.paused = True
    
    result = resume_from_confirmation(state, HumanInterventionType.CONFIRM)
    
    # Should return state update
    assert isinstance(result, dict)
    assert "confirmation" in result
    assert "paused" in result


# ─── CHECKPOINT INTEGRATION TESTS ────────────────────────────────────────────

def test_confirmation_checkpoint_can_be_loaded():
    """Test that checkpoint created by confirmation can be loaded."""
    from graph.checkpointing import get_checkpointing_service
    
    task = TaskRequest(user_input="delete file", source=TaskSource.VOICE)
    state = OperonixState(task=task)
    
    result = confirmation_node(state)
    
    checkpointing_service = get_checkpointing_service()
    checkpoint = checkpointing_service.load_checkpoint(state.checkpoint_identifier)
    
    # Checkpoint should exist
    assert checkpoint is not None
    assert checkpoint.task_id == task.task_id


def test_confirmation_checkpoint_state_restoration():
    """Test that state can be restored from checkpoint."""
    from graph.checkpointing import get_checkpointing_service
    
    task = TaskRequest(user_input="delete file", source=TaskSource.VOICE)
    state = OperonixState(task=task)
    
    result = confirmation_node(state)
    
    checkpointing_service = get_checkpointing_service()
    checkpoint = checkpointing_service.load_checkpoint(state.checkpoint_identifier)
    
    # Restore state from checkpoint
    restored_state = checkpointing_service.restore_state(checkpoint)
    
    # Restored state should have confirmation
    assert restored_state is not None
    assert restored_state.confirmation is not None
    # Confirmation should be a HumanIntervention object, not a dict
    assert isinstance(restored_state.confirmation, HumanIntervention)
    # Should be able to access confirmation attributes
    assert restored_state.confirmation.task_id == task.task_id


def test_confirmation_checkpoint_preserves_paused_state():
    """Test that checkpoint preserves paused state."""
    from graph.checkpointing import get_checkpointing_service
    
    task = TaskRequest(user_input="delete file", source=TaskSource.VOICE)
    state = OperonixState(task=task)
    
    result = confirmation_node(state)
    
    checkpointing_service = get_checkpointing_service()
    checkpoint = checkpointing_service.load_checkpoint(state.checkpoint_identifier)
    
    # Restore state from checkpoint
    restored_state = checkpointing_service.restore_state(checkpoint)
    
    # Restored state should be paused
    assert restored_state.paused is True


# ─── COMPLETE PAUSE/RESUME FLOW TESTS ───────────────────────────────────────

def test_complete_pause_resume_flow():
    """Test complete pause and resume flow."""
    from graph.checkpointing import get_checkpointing_service
    
    task = TaskRequest(user_input="delete file", source=TaskSource.VOICE)
    state = OperonixState(task=task)
    
    # Step 1: Pause at confirmation
    confirmation_node(state)
    
    assert state.paused is True
    assert state.checkpoint_identifier is not None
    assert state.confirmation is not None
    
    # Step 2: Load checkpoint
    checkpointing_service = get_checkpointing_service()
    checkpoint = checkpointing_service.load_checkpoint(state.checkpoint_identifier)
    assert checkpoint is not None
    
    # Step 3: Restore state
    restored_state = checkpointing_service.restore_state(checkpoint)
    assert restored_state.paused is True
    assert isinstance(restored_state.confirmation, HumanIntervention)
    
    # Step 4: Resume with human response
    resume_from_confirmation(restored_state, HumanInterventionType.CONFIRM)
    
    # Step 5: Verify resumed state
    assert restored_state.paused is False
    assert restored_state.confirmation.response == HumanInterventionType.CONFIRM
    assert restored_state.confirmation.responded_at is not None


if __name__ == "__main__":
    pytest.main([__file__, "-v"])
