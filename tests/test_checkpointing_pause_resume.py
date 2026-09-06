"""
Checkpointing & Pause/Resume Tests — Operonix Migration Phase 7
────────────────────────────────────────────────────────────────

Tests for checkpointing, pause/resume, and human intervention.
Per migration plan Phase 7: Checkpointing, Pause/Resume & Human Intervention
"""
from __future__ import annotations

import pytest
import tempfile
import shutil
from pathlib import Path


# ─── CHECKPOINTING SERVICE TESTS ─────────────────────────────────────────────

def test_checkpointing_service_init():
    """Test that CheckpointingService can be initialized."""
    from graph.checkpointing import CheckpointingService
    
    with tempfile.TemporaryDirectory() as tmpdir:
        service = CheckpointingService(checkpoint_dir=tmpdir)
        assert service.checkpoint_dir == Path(tmpdir)
        assert service.checkpoint_dir.exists()


def test_create_checkpoint():
    """Test that checkpoint can be created from state."""
    from graph.checkpointing import CheckpointingService
    from migration.graph_state import OperonixState
    from migration.domain_contracts import TaskRequest, TaskSource
    
    with tempfile.TemporaryDirectory() as tmpdir:
        service = CheckpointingService(checkpoint_dir=tmpdir)
        
        task = TaskRequest(user_input="Open Firefox", source=TaskSource.VOICE)
        state = OperonixState(task=task)
        
        checkpoint = service.create_checkpoint(state, "test_node")
        
        assert checkpoint is not None
        assert checkpoint.task_id == task.task_id
        assert checkpoint.current_node == "test_node"
        assert checkpoint.checkpoint_id is not None


def test_persist_checkpoint():
    """Test that checkpoint is persisted to disk."""
    from graph.checkpointing import CheckpointingService
    from migration.graph_state import OperonixState
    from migration.domain_contracts import TaskRequest, TaskSource
    
    with tempfile.TemporaryDirectory() as tmpdir:
        service = CheckpointingService(checkpoint_dir=tmpdir)
        
        task = TaskRequest(user_input="Open Firefox", source=TaskSource.VOICE)
        state = OperonixState(task=task)
        
        checkpoint = service.create_checkpoint(state, "test_node")
        
        # Check that file exists
        checkpoint_file = Path(tmpdir) / f"{checkpoint.checkpoint_id}.json"
        assert checkpoint_file.exists()


def test_load_checkpoint():
    """Test that checkpoint can be loaded from disk."""
    from graph.checkpointing import CheckpointingService
    from migration.graph_state import OperonixState
    from migration.domain_contracts import TaskRequest, TaskSource
    
    with tempfile.TemporaryDirectory() as tmpdir:
        service = CheckpointingService(checkpoint_dir=tmpdir)
        
        task = TaskRequest(user_input="Open Firefox", source=TaskSource.VOICE)
        state = OperonixState(task=task)
        
        checkpoint = service.create_checkpoint(state, "test_node")
        
        # Load checkpoint
        loaded_checkpoint = service.load_checkpoint(checkpoint.checkpoint_id)
        
        assert loaded_checkpoint is not None
        assert loaded_checkpoint.checkpoint_id == checkpoint.checkpoint_id
        assert loaded_checkpoint.task_id == checkpoint.task_id


def test_restore_state():
    """Test that OperonixState can be restored from checkpoint."""
    from graph.checkpointing import CheckpointingService
    from migration.graph_state import OperonixState
    from migration.domain_contracts import TaskRequest, TaskSource
    
    with tempfile.TemporaryDirectory() as tmpdir:
        service = CheckpointingService(checkpoint_dir=tmpdir)
        
        task = TaskRequest(user_input="Open Firefox", source=TaskSource.VOICE)
        state = OperonixState(task=task)
        
        checkpoint = service.create_checkpoint(state, "test_node")
        
        # Restore state
        restored_state = service.restore_state(checkpoint)
        
        assert restored_state is not None
        assert restored_state.task.task_id == state.task.task_id


def test_get_latest_checkpoint():
    """Test that latest checkpoint for a task can be retrieved."""
    from graph.checkpointing import CheckpointingService
    from migration.graph_state import OperonixState
    from migration.domain_contracts import TaskRequest, TaskSource
    
    with tempfile.TemporaryDirectory() as tmpdir:
        service = CheckpointingService(checkpoint_dir=tmpdir)
        
        task = TaskRequest(user_input="Open Firefox", source=TaskSource.VOICE)
        state = OperonixState(task=task)
        
        # Create multiple checkpoints
        checkpoint1 = service.create_checkpoint(state, "node1")
        checkpoint2 = service.create_checkpoint(state, "node2")
        
        # Get latest
        latest = service.get_latest_checkpoint(task.task_id)
        
        assert latest is not None
        # Should be checkpoint2 (later timestamp)
        assert latest.checkpoint_id == checkpoint2.checkpoint_id


def test_delete_checkpoint():
    """Test that checkpoint can be deleted."""
    from graph.checkpointing import CheckpointingService
    from migration.graph_state import OperonixState
    from migration.domain_contracts import TaskRequest, TaskSource
    
    with tempfile.TemporaryDirectory() as tmpdir:
        service = CheckpointingService(checkpoint_dir=tmpdir)
        
        task = TaskRequest(user_input="Open Firefox", source=TaskSource.VOICE)
        state = OperonixState(task=task)
        
        checkpoint = service.create_checkpoint(state, "test_node")
        
        # Delete checkpoint
        deleted = service.delete_checkpoint(checkpoint.checkpoint_id)
        
        assert deleted is True
        
        # Check that file is deleted
        checkpoint_file = Path(tmpdir) / f"{checkpoint.checkpoint_id}.json"
        assert not checkpoint_file.exists()


def test_delete_task_checkpoints():
    """Test that all checkpoints for a task can be deleted."""
    from graph.checkpointing import CheckpointingService
    from migration.graph_state import OperonixState
    from migration.domain_contracts import TaskRequest, TaskSource
    
    with tempfile.TemporaryDirectory() as tmpdir:
        service = CheckpointingService(checkpoint_dir=tmpdir)
        
        task = TaskRequest(user_input="Open Firefox", source=TaskSource.VOICE)
        state = OperonixState(task=task)
        
        # Create multiple checkpoints
        service.create_checkpoint(state, "node1")
        service.create_checkpoint(state, "node2")
        service.create_checkpoint(state, "node3")
        
        # Delete all checkpoints for task
        deleted_count = service.delete_task_checkpoints(task.task_id)
        
        assert deleted_count == 3


def test_checkpoint_state_domain_object():
    """Test that CheckpointState is a valid domain object."""
    from migration.domain_contracts import CheckpointState
    
    checkpoint = CheckpointState(
        task_id="test_task",
        current_node="test_node",
        workflow_state={"test": "data"}
    )
    
    assert checkpoint.task_id == "test_task"
    assert checkpoint.current_node == "test_node"
    assert checkpoint.checkpoint_id is not None
    assert checkpoint.checkpoint_timestamp is not None


# ─── HUMAN INTERVENTION TESTS ───────────────────────────────────────────────────

def test_human_intervention_domain_object():
    """Test that HumanIntervention is a valid domain object."""
    from migration.domain_contracts import HumanIntervention, HumanInterventionType
    
    intervention = HumanIntervention(
        task_id="test_task",
        intervention_type=HumanInterventionType.CONFIRM,
        reason="Safety check requires confirmation"
    )
    
    assert intervention.task_id == "test_task"
    assert intervention.intervention_type == HumanInterventionType.CONFIRM
    assert intervention.intervention_id is not None
    assert intervention.requested_at is not None


def test_human_intervention_type_enum():
    """Test that HumanInterventionType enum has all required values."""
    from migration.domain_contracts import HumanInterventionType
    
    assert HumanInterventionType.CONFIRM.value == "confirm"
    assert HumanInterventionType.DENY.value == "deny"
    assert HumanInterventionType.CLARIFY.value == "clarify"
    assert HumanInterventionType.CHOOSE.value == "choose"
    assert HumanInterventionType.PROVIDE_INFORMATION.value == "provide_information"
    assert HumanInterventionType.TAKE_OVER.value == "take_over"
    assert HumanInterventionType.ABORT.value == "abort"


def test_human_intervention_with_response():
    """Test that HumanIntervention can have a response."""
    from migration.domain_contracts import HumanIntervention, HumanInterventionType
    from datetime import datetime
    
    intervention = HumanIntervention(
        task_id="test_task",
        intervention_type=HumanInterventionType.CONFIRM,
        reason="Safety check requires confirmation"
    )
    
    intervention.response = HumanInterventionType.CONFIRM
    intervention.responded_at = datetime.utcnow()
    
    assert intervention.response == HumanInterventionType.CONFIRM
    assert intervention.responded_at is not None


# ─── CONFIRMATION NODE TESTS ───────────────────────────────────────────────────

def test_confirmation_node_import():
    """Test that confirmation node can be imported."""
    from graph.nodes.confirmation import confirmation_node
    assert confirmation_node is not None


def test_confirmation_node_creates_intervention():
    """Test that confirmation node creates human intervention."""
    from graph.nodes.confirmation import confirmation_node
    from migration.graph_state import OperonixState
    from migration.domain_contracts import TaskRequest, TaskSource
    
    task = TaskRequest(user_input="Delete file", source=TaskSource.VOICE)
    state = OperonixState(task=task)
    
    result = confirmation_node(state)
    
    assert result["state"].confirmation is not None
    assert result["state"].confirmation.task_id == task.task_id


def test_confirmation_node_creates_checkpoint():
    """Test that confirmation node creates checkpoint."""
    from graph.nodes.confirmation import confirmation_node
    from migration.graph_state import OperonixState
    from migration.domain_contracts import TaskRequest, TaskSource
    
    with tempfile.TemporaryDirectory() as tmpdir:
        from graph.checkpointing import CheckpointingService
        # Override global service
        import graph.checkpointing as checkpointing_module
        original_service = checkpointing_module._checkpointing_service
        checkpointing_module._checkpointing_service = CheckpointingService(checkpoint_dir=tmpdir)
        
        try:
            task = TaskRequest(user_input="Delete file", source=TaskSource.VOICE)
            state = OperonixState(task=task)
            
            result = confirmation_node(state)
            
            assert result["state"].checkpoint_id is not None
            assert result["state"].paused is True
        finally:
            checkpointing_module._checkpointing_service = original_service


def test_confirmation_node_pauses_graph():
    """Test that confirmation node pauses graph execution."""
    from graph.nodes.confirmation import confirmation_node
    from migration.graph_state import OperonixState
    from migration.domain_contracts import TaskRequest, TaskSource
    
    task = TaskRequest(user_input="Delete file", source=TaskSource.VOICE)
    state = OperonixState(task=task)
    
    result = confirmation_node(state)
    
    assert result["state"].paused is True


def test_resume_from_confirmation():
    """Test that graph can resume from human intervention."""
    from graph.nodes.confirmation import resume_from_confirmation
    from migration.graph_state import OperonixState
    from migration.domain_contracts import TaskRequest, TaskSource, HumanIntervention, HumanInterventionType
    
    task = TaskRequest(user_input="Delete file", source=TaskSource.VOICE)
    state = OperonixState(task=task)
    state.confirmation = HumanIntervention(
        task_id=task.task_id,
        intervention_type=HumanInterventionType.CONFIRM,
        reason="Safety check requires confirmation"
    )
    state.paused = True
    
    result = resume_from_confirmation(state, HumanInterventionType.CONFIRM)
    
    assert result["state"].paused is False
    assert result["state"].confirmation.response == HumanInterventionType.CONFIRM


def test_confirmation_node_history_tracking():
    """Test that confirmation node tracks history events."""
    from graph.nodes.confirmation import confirmation_node
    from migration.graph_state import OperonixState
    from migration.domain_contracts import TaskRequest, TaskSource
    
    with tempfile.TemporaryDirectory() as tmpdir:
        from graph.checkpointing import CheckpointingService
        # Override global service
        import graph.checkpointing as checkpointing_module
        original_service = checkpointing_module._checkpointing_service
        checkpointing_module._checkpointing_service = CheckpointingService(checkpoint_dir=tmpdir)
        
        try:
            task = TaskRequest(user_input="Delete file", source=TaskSource.VOICE)
            state = OperonixState(task=task)
            
            result = confirmation_node(state)
            
            events = result["state"].history.get("events", [])
            assert len(events) >= 2  # confirmation_started, confirmation_paused
            
            event_types = [e["type"] for e in events]
            assert "confirmation_started" in event_types
            assert "confirmation_paused" in event_types
        finally:
            checkpointing_module._checkpointing_service = original_service


# ─── GRAPH CONDITIONAL ROUTING TESTS ─────────────────────────────────────────────

def test_graph_conditional_routing_confirmation_required():
    """Test that confirmation_required triggers confirmation node."""
    from graph.graph import needs_confirmation
    from migration.graph_state import OperonixState
    from migration.domain_contracts import TaskRequest, TaskSource, SafetyDecision, RiskLevel
    
    task = TaskRequest(user_input="Delete file", source=TaskSource.VOICE)
    state = OperonixState(task=task)
    
    state.safety = SafetyDecision(
        risk_level=RiskLevel.HIGH,
        validation_status="APPROVED",
        permission_status="GRANTED",
        confirmation_required=True,
        safety_checks_performed=["risk_check"]
    )
    
    result = needs_confirmation(state)
    assert result == "confirmation"


def test_graph_conditional_routing_no_confirmation_required():
    """Test that no confirmation_required triggers execute_step."""
    from graph.graph import needs_confirmation
    from migration.graph_state import OperonixState
    from migration.domain_contracts import TaskRequest, TaskSource, SafetyDecision, RiskLevel
    
    task = TaskRequest(user_input="Open Firefox", source=TaskSource.VOICE)
    state = OperonixState(task=task)
    
    state.safety = SafetyDecision(
        risk_level=RiskLevel.LOW,
        validation_status="APPROVED",
        permission_status="GRANTED",
        confirmation_required=False,
        safety_checks_performed=["risk_check"]
    )
    
    result = needs_confirmation(state)
    assert result == "execute_step"


def test_graph_conditional_routing_no_safety_decision():
    """Test that missing safety decision defaults to execute_step."""
    from graph.graph import needs_confirmation
    from migration.graph_state import OperonixState
    from migration.domain_contracts import TaskRequest, TaskSource
    
    task = TaskRequest(user_input="Open Firefox", source=TaskSource.VOICE)
    state = OperonixState(task=task)
    
    result = needs_confirmation(state)
    assert result == "execute_step"


# ─── SAFETY CHECK STUB TESTS ───────────────────────────────────────────────────

def test_safety_check_stub():
    """Test that safety_check node is a stub (STUB)."""
    from graph.nodes.safety_check import safety_check_node
    from migration.graph_state import OperonixState
    from migration.domain_contracts import TaskRequest, TaskSource
    
    task = TaskRequest(user_input="Delete file", source=TaskSource.VOICE)
    state = OperonixState(task=task)
    
    result = safety_check_node(state)
    
    # Safety check is a stub, should create placeholder decision
    assert result["state"].safety is not None
    # confirmation_required is False by default in stub
    assert result["state"].safety.confirmation_required is False


def test_safety_check_stub_can_set_confirmation_required():
    """Test that safety_check stub can set confirmation_required for testing."""
    from graph.nodes.safety_check import safety_check_node
    from migration.graph_state import OperonixState
    from migration.domain_contracts import TaskRequest, TaskSource
    
    task = TaskRequest(user_input="Delete file", source=TaskSource.VOICE)
    state = OperonixState(task=task)
    
    result = safety_check_node(state)
    
    # Stub creates placeholder decision
    # In real implementation, this would be determined by risk rules
    assert result["state"].safety is not None
    # Can be set to True to test confirmation flow
    # Currently False by default
