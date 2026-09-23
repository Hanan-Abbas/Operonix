"""
tests/test_resume_manager.py

Unit tests for ResumeManager — External Resume Mechanism
────────────────────────────────────────────────────────

Tests the resume manager's ability to:
- List pending confirmations
- Get confirmation details
- Resume workflows with human response
- Cancel confirmations
- Handle EventBus integration
"""
from __future__ import annotations

import pytest
import asyncio
from unittest.mock import Mock, MagicMock, patch
from datetime import timezone

from migration.domain_contracts import HumanInterventionType, HumanIntervention, TaskRequest, TaskSource, CheckpointState
from migration.graph_state import OperonixState
from graph.resume_manager import ResumeManager


@pytest.fixture
def mock_event_bus():
    """Create a mock EventBus."""
    bus = Mock()
    bus.subscribe = Mock()
    return bus


@pytest.fixture
def resume_manager(mock_event_bus):
    """Create a ResumeManager instance with mock EventBus."""
    return ResumeManager(event_bus=mock_event_bus)


@pytest.fixture
def sample_task_request():
    """Create a sample task request."""
    return TaskRequest(
        task_id="test-task-123",
        user_input="List files in /tmp",
        source=TaskSource.PANEL
    )


@pytest.fixture
def sample_state(sample_task_request):
    """Create a sample OperonixState with confirmation."""
    state = OperonixState(task=sample_task_request)
    state.paused = True
    state.checkpoint_identifier = "test-checkpoint-123"
    
    # Add human intervention
    state.confirmation = HumanIntervention(
        task_id=sample_task_request.task_id,
        intervention_type=HumanInterventionType.CONFIRM,
        reason="Safety check requires confirmation",
        context={
            "intent": "list_files",
            "parameters": {"path": "/tmp"}
        }
    )
    
    return state


@pytest.fixture
def sample_checkpoint(sample_state):
    """Create a sample checkpoint."""
    return CheckpointState(
        checkpoint_identifier="test-checkpoint-123",
        task_id=sample_state.task.task_id,
        workflow_state=sample_state.model_dump(),
        current_node="confirmation",
        current_plan_step_index=0,
        completed_steps=[],
        routing_decision=None,
        safety_state=None,
        confirmation_state=sample_state.confirmation.model_dump() if sample_state.confirmation else None,
        execution_status=None,
        recovery_data=None,
        relevant_context=sample_state.context.model_dump() if sample_state.context else {}
    )


class TestResumeManagerInitialization:
    """Test ResumeManager initialization."""
    
    def test_init_with_event_bus(self, mock_event_bus):
        """Test initialization with EventBus."""
        manager = ResumeManager(event_bus=mock_event_bus)
        
        assert manager._event_bus == mock_event_bus
        mock_event_bus.subscribe.assert_called_once_with(
            "user_response_received",
            manager._on_user_response_received
        )
    
    def test_init_without_event_bus(self):
        """Test initialization without EventBus."""
        manager = ResumeManager(event_bus=None)
        
        assert manager._event_bus is None


class TestGetPendingConfirmations:
    """Test getting pending confirmations."""
    
    def test_get_pending_confirmations_empty(self, resume_manager):
        """Test getting pending confirmations when none exist."""
        with patch.object(resume_manager._checkpointing_service, 'checkpoint_dir', '/tmp/nonexistent'):
            confirmations = resume_manager.get_pending_confirmations()
            
            assert confirmations == []
    
    def test_get_pending_confirmations_with_data(self, resume_manager, sample_checkpoint):
        """Test getting pending confirmations with paused workflows."""
        with patch.object(resume_manager._checkpointing_service, 'checkpoint_dir', '/tmp/test'):
            with patch('os.path.exists', return_value=True):
                with patch('os.listdir', return_value=['test-checkpoint-123.json']):
                    with patch.object(
                        resume_manager._checkpointing_service,
                        'load_checkpoint',
                        return_value=sample_checkpoint
                    ):
                        confirmations = resume_manager.get_pending_confirmations()
                        
                        assert len(confirmations) == 1
                        assert confirmations[0]['task_id'] == 'test-task-123'
                        assert confirmations[0]['intervention_type'] == 'confirm'
                        assert confirmations[0]['checkpoint_identifier'] == 'test-checkpoint-123'


class TestGetConfirmation:
    """Test getting confirmation details for a specific task."""
    
    def test_get_confirmation_not_found(self, resume_manager):
        """Test getting confirmation for non-existent task."""
        with patch.object(
            resume_manager._checkpointing_service,
            'get_latest_checkpoint',
            return_value=None
        ):
            confirmation = resume_manager.get_confirmation('nonexistent-task')
            
            assert confirmation is None
    
    def test_get_confirmation_not_paused(self, resume_manager, sample_checkpoint):
        """Test getting confirmation for task that is not paused."""
        # Modify state to not be paused
        state_dict = sample_checkpoint.workflow_state.copy()
        state_dict['paused'] = False
        sample_checkpoint.workflow_state = state_dict
        
        with patch.object(
            resume_manager._checkpointing_service,
            'get_latest_checkpoint',
            return_value=sample_checkpoint
        ):
            confirmation = resume_manager.get_confirmation('test-task-123')
            
            assert confirmation is None
    
    def test_get_confirmation_success(self, resume_manager, sample_checkpoint):
        """Test successfully getting confirmation details."""
        with patch.object(
            resume_manager._checkpointing_service,
            'get_latest_checkpoint',
            return_value=sample_checkpoint
        ):
            confirmation = resume_manager.get_confirmation('test-task-123')
            
            assert confirmation is not None
            assert confirmation['task_id'] == 'test-task-123'
            assert confirmation['intervention_type'] == 'confirm'
            assert confirmation['reason'] == 'Safety check requires confirmation'
            assert confirmation['checkpoint_identifier'] == 'test-checkpoint-123'


class TestResumeWorkflow:
    """Test resuming workflows with human response."""
    
    def test_resume_workflow_invalid_response(self, resume_manager):
        """Test resuming with invalid response type."""
        result = resume_manager.resume_workflow('test-task', 'INVALID_RESPONSE')
        
        assert result['status'] == 'error'
        assert 'Invalid response' in result['message']
    
    def test_resume_workflow_no_checkpoint(self, resume_manager):
        """Test resuming when no checkpoint exists."""
        with patch.object(
            resume_manager._checkpointing_service,
            'get_latest_checkpoint',
            return_value=None
        ):
            result = resume_manager.resume_workflow('test-task', 'confirm')
            
            assert result['status'] == 'error'
            assert 'No checkpoint found' in result['message']
    
    def test_resume_workflow_not_paused(self, resume_manager, sample_checkpoint):
        """Test resuming when task is not paused."""
        state_dict = sample_checkpoint.workflow_state.copy()
        state_dict['paused'] = False
        sample_checkpoint.workflow_state = state_dict
        
        with patch.object(
            resume_manager._checkpointing_service,
            'get_latest_checkpoint',
            return_value=sample_checkpoint
        ):
            result = resume_manager.resume_workflow('test-task-123', 'confirm')
            
            assert result['status'] == 'error'
            assert 'not paused' in result['message']
    
    def test_resume_workflow_restore_failed(self, resume_manager, sample_checkpoint):
        """Test resuming when state restoration fails."""
        with patch.object(
            resume_manager._checkpointing_service,
            'get_latest_checkpoint',
            return_value=sample_checkpoint
        ):
            with patch.object(
                resume_manager._checkpointing_service,
                'restore_state',
                return_value=None
            ):
                result = resume_manager.resume_workflow('test-task-123', 'confirm')
                
                assert result['status'] == 'error'
                assert 'Failed to restore state' in result['message']
    
    def test_resume_workflow_graph_not_available(self, resume_manager, sample_checkpoint, sample_state):
        """Test resuming when graph runner is not available."""
        with patch.object(
            resume_manager._checkpointing_service,
            'get_latest_checkpoint',
            return_value=sample_checkpoint
        ):
            with patch.object(
                resume_manager._checkpointing_service,
                'restore_state',
                return_value=sample_state
            ):
                with patch('graph.graph.graph_runner', None):
                    result = resume_manager.resume_workflow('test-task-123', 'confirm')
                    
                    assert result['status'] == 'error'
                    assert 'Graph runner not available' in result['message']


class TestCancelConfirmation:
    """Test canceling pending confirmations."""
    
    def test_cancel_confirmation_not_found(self, resume_manager):
        """Test canceling confirmation for non-existent task."""
        with patch.object(
            resume_manager._checkpointing_service,
            'get_latest_checkpoint',
            return_value=None
        ):
            result = resume_manager.cancel_confirmation('nonexistent-task')
            
            assert result['status'] == 'error'
            assert 'No checkpoint found' in result['message']
    
    def test_cancel_confirmation_success(self, resume_manager, sample_checkpoint):
        """Test successfully canceling confirmation."""
        with patch.object(
            resume_manager._checkpointing_service,
            'get_latest_checkpoint',
            return_value=sample_checkpoint
        ):
            with patch.object(
                resume_manager._checkpointing_service,
                'delete_checkpoint'
            ) as mock_delete:
                result = resume_manager.cancel_confirmation('test-task-123')
                
                assert result['status'] == 'success'
                assert result['task_id'] == 'test-task-123'
                mock_delete.assert_called_once_with('test-checkpoint-123')


class TestEventBusIntegration:
    """Test EventBus integration for panel responses."""
    
    def test_on_user_response_received(self, resume_manager, sample_state):
        """Test handling user response from EventBus."""
        event = Mock()
        event.data = {
            'task_id': 'test-task-123',
            'response': 'confirm',
            'response_data': {'note': 'user note'}
        }
        
        with patch.object(resume_manager, 'resume_workflow') as mock_resume:
            resume_manager._on_user_response_received(event)
            
            mock_resume.assert_called_once_with(
                'test-task-123',
                'confirm',
                {'note': 'user note'}
            )
    
    def test_on_user_response_received_invalid_payload(self, resume_manager):
        """Test handling invalid payload from EventBus."""
        event = Mock()
        event.data = "invalid"
        
        with patch.object(resume_manager, 'resume_workflow') as mock_resume:
            resume_manager._on_user_response_received(event)
            
            mock_resume.assert_not_called()
    
    def test_on_user_response_received_missing_fields(self, resume_manager):
        """Test handling payload with missing required fields."""
        event = Mock()
        event.data = {'task_id': 'test-task'}  # Missing response
        
        with patch.object(resume_manager, 'resume_workflow') as mock_resume:
            resume_manager._on_user_response_received(event)
            
            mock_resume.assert_not_called()


class TestGetResumeManager:
    """Test global resume manager singleton."""
    
    def test_get_resume_manager_creates_instance(self):
        """Test that get_resume_manager creates a new instance."""
        # Reset global
        from graph.resume_manager import resume_manager
        import graph.resume_manager as rm_module
        rm_module.resume_manager = None
        
        manager = rm_module.get_resume_manager()
        
        assert manager is not None
        assert isinstance(manager, ResumeManager)
    
    def test_get_resume_manager_returns_singleton(self):
        """Test that get_resume_manager returns the same instance."""
        from graph.resume_manager import resume_manager as global_manager
        import graph.resume_manager as rm_module
        
        if global_manager is None:
            rm_module.resume_manager = ResumeManager()
        
        manager1 = rm_module.get_resume_manager()
        manager2 = rm_module.get_resume_manager()
        
        assert manager1 is manager2
