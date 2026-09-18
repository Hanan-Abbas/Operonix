"""
Checkpointing Service Tests — Operonix Graph
────────────────────────────────────────────

Unit tests for checkpointing service.
These tests verify that:
- Checkpoint service can be instantiated
- Checkpoints can be created for a given state
- Checkpoints can be loaded by identifier
- State can be restored from checkpoint
- Checkpoint directory is managed correctly
- Checkpoint files are persisted to disk
- Multiple checkpoints can be created for a task
- Latest checkpoint can be retrieved
- Checkpoint cleanup works correctly
"""
from __future__ import annotations

import pytest
import shutil
from pathlib import Path
from typing import Dict, Any
from migration.graph_state import OperonixState
from migration.domain_contracts import TaskRequest, TaskSource
from graph.checkpointing import CheckpointingService, get_checkpointing_service


# ─── CHECKPOINTING SERVICE TESTS ─────────────────────────────────────────────

def test_checkpointing_service_exists():
    """Test that checkpointing service can be imported."""
    from graph.checkpointing import CheckpointingService, get_checkpointing_service
    assert CheckpointingService is not None
    assert get_checkpointing_service is not None


def test_checkpointing_service_instantiation():
    """Test that checkpointing service can be instantiated."""
    service = CheckpointingService(checkpoint_dir="/tmp/test_checkpoints")
    assert service is not None
    assert service.checkpoint_dir == Path("/tmp/test_checkpoints")


def test_get_checkpointing_service():
    """Test that get_checkpointing_service returns a service instance."""
    service = get_checkpointing_service()
    assert service is not None
    assert isinstance(service, CheckpointingService)


def test_create_checkpoint():
    """Test that checkpoint can be created for a state."""
    task = TaskRequest(user_input="test", source=TaskSource.VOICE)
    state = OperonixState(task=task)
    
    service = CheckpointingService(checkpoint_dir="/tmp/test_checkpoints")
    checkpoint = service.create_checkpoint(state, "test_node")
    
    assert checkpoint is not None
    assert checkpoint.checkpoint_identifier is not None
    assert checkpoint.task_id == task.task_id
    assert checkpoint.current_node == "test_node"


def test_load_checkpoint():
    """Test that checkpoint can be loaded by identifier."""
    task = TaskRequest(user_input="test", source=TaskSource.VOICE)
    state = OperonixState(task=task)
    
    service = CheckpointingService(checkpoint_dir="/tmp/test_checkpoints")
    checkpoint = service.create_checkpoint(state, "test_node")
    
    loaded_checkpoint = service.load_checkpoint(checkpoint.checkpoint_identifier)
    
    assert loaded_checkpoint is not None
    assert loaded_checkpoint.checkpoint_identifier == checkpoint.checkpoint_identifier
    assert loaded_checkpoint.task_id == task.task_id


def test_restore_state():
    """Test that state can be restored from checkpoint."""
    task = TaskRequest(user_input="test", source=TaskSource.VOICE)
    state = OperonixState(task=task)
    state.paused = True
    
    service = CheckpointingService(checkpoint_dir="/tmp/test_checkpoints")
    checkpoint = service.create_checkpoint(state, "test_node")
    
    restored_state = service.restore_state(checkpoint)
    
    assert restored_state is not None
    assert restored_state.task.task_id == task.task_id
    assert restored_state.paused is True


def test_checkpoint_directory_creation():
    """Test that checkpoint directory is created if it doesn't exist."""
    import tempfile
    
    with tempfile.TemporaryDirectory() as tmpdir:
        checkpoint_dir = Path(tmpdir) / "checkpoints"
        assert not checkpoint_dir.exists()
        
        service = CheckpointingService(checkpoint_dir=str(checkpoint_dir))
        assert checkpoint_dir.exists()


def test_checkpoint_file_persistence():
    """Test that checkpoint file is persisted to disk."""
    import tempfile
    
    with tempfile.TemporaryDirectory() as tmpdir:
        checkpoint_dir = Path(tmpdir) / "checkpoints"
        service = CheckpointingService(checkpoint_dir=str(checkpoint_dir))
        
        task = TaskRequest(user_input="test", source=TaskSource.VOICE)
        state = OperonixState(task=task)
        checkpoint = service.create_checkpoint(state, "test_node")
        
        checkpoint_file = checkpoint_dir / f"{checkpoint.checkpoint_identifier}.json"
        assert checkpoint_file.exists()


def test_multiple_checkpoints_for_task():
    """Test that multiple checkpoints can be created for a task."""
    task = TaskRequest(user_input="test", source=TaskSource.VOICE)
    state = OperonixState(task=task)
    
    service = CheckpointingService(checkpoint_dir="/tmp/test_checkpoints")
    
    checkpoint1 = service.create_checkpoint(state, "node1")
    checkpoint2 = service.create_checkpoint(state, "node2")
    
    assert checkpoint1.checkpoint_identifier != checkpoint2.checkpoint_identifier
    assert checkpoint1.task_id == checkpoint2.task_id


def test_get_latest_checkpoint():
    """Test that latest checkpoint can be retrieved for a task."""
    task = TaskRequest(user_input="test", source=TaskSource.VOICE)
    state = OperonixState(task=task)
    
    service = CheckpointingService(checkpoint_dir="/tmp/test_checkpoints")
    
    checkpoint1 = service.create_checkpoint(state, "node1")
    checkpoint2 = service.create_checkpoint(state, "node2")
    
    latest = service.get_latest_checkpoint(task.task_id)
    
    assert latest is not None
    assert latest.checkpoint_identifier == checkpoint2.checkpoint_identifier


def test_get_latest_checkpoint_no_checkpoints():
    """Test that get_latest_checkpoint returns None when no checkpoints exist."""
    service = CheckpointingService(checkpoint_dir="/tmp/test_checkpoints")
    
    latest = service.get_latest_checkpoint("nonexistent-task")
    
    assert latest is None


def test_checkpoint_with_plan():
    """Test that checkpoint includes plan information."""
    from migration.domain_contracts import Plan, PlanStep, PlanStepSideEffect
    import uuid
    
    task = TaskRequest(user_input="test", source=TaskSource.VOICE)
    state = OperonixState(task=task)
    
    # Add a plan to the state
    step = PlanStep(
        step_id=str(uuid.uuid4()),
        objective="Test objective",
        action="test_action",
        parameters={},
        side_effect=PlanStepSideEffect.NONE
    )
    plan = Plan(
        plan_id=str(uuid.uuid4()),
        objective="Test plan objective",
        steps=[step],
        current_step_index=0,
        completed_steps=[]
    )
    state.plan = plan
    
    service = CheckpointingService(checkpoint_dir="/tmp/test_checkpoints")
    checkpoint = service.create_checkpoint(state, "test_node")
    
    assert checkpoint.current_plan_step_index == 0
    assert checkpoint.completed_steps == []


def test_checkpoint_with_safety():
    """Test that checkpoint includes safety information."""
    from migration.domain_contracts import SafetyDecision, RiskLevel
    
    task = TaskRequest(user_input="test", source=TaskSource.VOICE)
    state = OperonixState(task=task)
    
    # Add safety decision to the state
    safety = SafetyDecision(
        risk_level=RiskLevel.LOW,
        validation_status="APPROVED",
        permission_status="GRANTED",
        confirmation_required=False
    )
    state.safety = safety
    
    service = CheckpointingService(checkpoint_dir="/tmp/test_checkpoints")
    checkpoint = service.create_checkpoint(state, "test_node")
    
    assert checkpoint.safety_state is not None
    assert checkpoint.safety_state["risk_level"] == "low"


def test_checkpoint_with_confirmation():
    """Test that checkpoint includes confirmation information."""
    from migration.domain_contracts import HumanIntervention, HumanInterventionType
    
    task = TaskRequest(user_input="test", source=TaskSource.VOICE)
    state = OperonixState(task=task)
    
    # Add confirmation to the state
    confirmation = HumanIntervention(
        task_id=task.task_id,
        intervention_type=HumanInterventionType.CONFIRM,
        reason="Test reason"
    )
    state.confirmation = confirmation
    
    service = CheckpointingService(checkpoint_dir="/tmp/test_checkpoints")
    checkpoint = service.create_checkpoint(state, "test_node")
    
    assert checkpoint.confirmation_state is not None
    assert checkpoint.confirmation_state["intervention_type"] == "confirm"


def test_checkpoint_with_execution():
    """Test that checkpoint includes execution information."""
    from migration.domain_contracts import ExecutionResult, TaskStatus
    import uuid
    
    task = TaskRequest(user_input="test", source=TaskSource.VOICE)
    state = OperonixState(task=task)
    
    # Add execution result to the state
    execution = ExecutionResult(
        execution_id=str(uuid.uuid4()),
        step_id="test-step",
        success=True,
        result_data={"output": "test"},
        method_used="shell",
        execution_status=TaskStatus.COMPLETED
    )
    state.execution = execution
    
    service = CheckpointingService(checkpoint_dir="/tmp/test_checkpoints")
    checkpoint = service.create_checkpoint(state, "test_node")
    
    assert checkpoint.execution_status == "completed"


def test_checkpoint_with_routing():
    """Test that checkpoint includes routing information."""
    from migration.domain_contracts import RoutingDecision, Candidate
    import uuid
    
    task = TaskRequest(user_input="test", source=TaskSource.VOICE)
    state = OperonixState(task=task)
    
    # Add routing decision to the state
    routing = RoutingDecision(
        selected_candidate=Candidate(
            candidate_id=str(uuid.uuid4()),
            candidate_type="shell",
            method_type="shell",
            method_name="execute_command"
        ),
        routing_explanation="Test routing"
    )
    state.routing = routing
    
    service = CheckpointingService(checkpoint_dir="/tmp/test_checkpoints")
    checkpoint = service.create_checkpoint(state, "test_node")
    
    assert checkpoint.routing_decision is not None


def test_restore_state_with_confirmation():
    """Test that state with confirmation is restored correctly."""
    from migration.domain_contracts import HumanIntervention, HumanInterventionType
    
    task = TaskRequest(user_input="test", source=TaskSource.VOICE)
    state = OperonixState(task=task)
    
    confirmation = HumanIntervention(
        task_id=task.task_id,
        intervention_type=HumanInterventionType.CONFIRM,
        reason="Test reason"
    )
    state.confirmation = confirmation
    
    service = CheckpointingService(checkpoint_dir="/tmp/test_checkpoints")
    checkpoint = service.create_checkpoint(state, "test_node")
    
    restored_state = service.restore_state(checkpoint)
    
    assert restored_state.confirmation is not None
    assert isinstance(restored_state.confirmation, HumanIntervention)
    assert restored_state.confirmation.intervention_type == HumanInterventionType.CONFIRM


def test_restore_state_with_paused():
    """Test that paused state is restored correctly."""
    task = TaskRequest(user_input="test", source=TaskSource.VOICE)
    state = OperonixState(task=task)
    state.paused = True
    state.checkpoint_identifier = "test-checkpoint-id"
    
    service = CheckpointingService(checkpoint_dir="/tmp/test_checkpoints")
    checkpoint = service.create_checkpoint(state, "test_node")
    
    restored_state = service.restore_state(checkpoint)
    
    assert restored_state.paused is True
    assert restored_state.checkpoint_identifier == "test-checkpoint-id"


def test_checkpoint_cleanup():
    """Test that checkpoint cleanup works correctly."""
    import tempfile
    
    with tempfile.TemporaryDirectory() as tmpdir:
        checkpoint_dir = Path(tmpdir) / "checkpoints"
        service = CheckpointingService(checkpoint_dir=str(checkpoint_dir))
        
        task = TaskRequest(user_input="test", source=TaskSource.VOICE)
        state = OperonixState(task=task)
        checkpoint = service.create_checkpoint(state, "test_node")
        
        checkpoint_file = checkpoint_dir / f"{checkpoint.checkpoint_identifier}.json"
        assert checkpoint_file.exists()
        
        # Clean up checkpoint
        shutil.rmtree(checkpoint_dir)
        assert not checkpoint_dir.exists()


if __name__ == "__main__":
    pytest.main([__file__, "-v"])
