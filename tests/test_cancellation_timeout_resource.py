"""
Cancellation, Timeout & Resource Control Tests — Operonix Migration Phase 8
────────────────────────────────────────────────────────────────────────────

Tests for cancellation, timeout, and resource control.
Per migration plan Phase 8: Cancellation, Timeout & Resource Control
"""
from __future__ import annotations

import pytest
import time


# ─── TIMEOUT CONFIG TESTS ───────────────────────────────────────────────────

def test_timeout_config_domain_object():
    """Test that TimeoutConfig is a valid domain object."""
    from migration.domain_contracts import TimeoutConfig
    
    config = TimeoutConfig(
        operation_timeout_seconds=30,
        step_timeout_seconds=120,
        task_timeout_seconds=300,
        system_watchdog_timeout_seconds=600
    )
    
    assert config.operation_timeout_seconds == 30
    assert config.step_timeout_seconds == 120
    assert config.task_timeout_seconds == 300
    assert config.system_watchdog_timeout_seconds == 600


def test_timeout_config_defaults():
    """Test that TimeoutConfig has sensible defaults."""
    from migration.domain_contracts import TimeoutConfig
    
    config = TimeoutConfig()
    
    assert config.operation_timeout_seconds == 30
    assert config.step_timeout_seconds == 120
    assert config.task_timeout_seconds == 300
    assert config.system_watchdog_timeout_seconds == 600


# ─── CANCELLATION REQUEST TESTS ─────────────────────────────────────────────

def test_cancellation_request_domain_object():
    """Test that CancellationRequest is a valid domain object."""
    from migration.domain_contracts import CancellationRequest, CancellationReason
    
    cancellation = CancellationRequest(
        task_id="test_task",
        reason=CancellationReason.USER_REQUESTED,
        requested_by="user"
    )
    
    assert cancellation.task_id == "test_task"
    assert cancellation.reason == CancellationReason.USER_REQUESTED
    assert cancellation.requested_by == "user"
    assert cancellation.cancellation_id is not None
    assert cancellation.requested_at is not None


def test_cancellation_reason_enum():
    """Test that CancellationReason enum has all required values."""
    from migration.domain_contracts import CancellationReason
    
    assert CancellationReason.USER_REQUESTED.value == "user_requested"
    assert CancellationReason.TIMEOUT.value == "timeout"
    assert CancellationReason.SAFE_ABORT.value == "safe_abort"
    assert CancellationReason.RESOURCE_CONTENTION.value == "resource_contention"
    assert CancellationReason.SYSTEM_ERROR.value == "system_error"
    assert CancellationReason.UNKNOWN.value == "unknown"


# ─── TIMEOUT MANAGER TESTS ───────────────────────────────────────────────────

def test_timeout_manager_init():
    """Test that TimeoutManager can be initialized."""
    from graph.timeout_manager import TimeoutManager
    
    manager = TimeoutManager()
    assert manager is not None
    assert manager.timeout_config is not None


def test_timeout_manager_with_custom_config():
    """Test that TimeoutManager can be initialized with custom config."""
    from graph.timeout_manager import TimeoutManager
    from migration.domain_contracts import TimeoutConfig
    
    config = TimeoutConfig(operation_timeout_seconds=60)
    manager = TimeoutManager(timeout_config=config)
    
    assert manager.timeout_config.operation_timeout_seconds == 60


def test_start_operation_timeout():
    """Test that operation timeout can be started."""
    from graph.timeout_manager import TimeoutManager
    
    manager = TimeoutManager()
    
    callback_called = []
    def callback(timeout_key, timeout_info):
        callback_called.append(timeout_key)
    
    manager.start_operation_timeout("task_1", "op_1", callback)
    
    active_timeouts = manager.get_active_timeouts("task_1")
    assert len(active_timeouts) == 1
    assert active_timeouts[0]["type"] == "operation"


def test_start_step_timeout():
    """Test that step timeout can be started."""
    from graph.timeout_manager import TimeoutManager
    
    manager = TimeoutManager()
    
    callback_called = []
    def callback(timeout_key, timeout_info):
        callback_called.append(timeout_key)
    
    manager.start_step_timeout("task_1", "step_1", callback)
    
    active_timeouts = manager.get_active_timeouts("task_1")
    assert len(active_timeouts) == 1
    assert active_timeouts[0]["type"] == "step"


def test_start_task_timeout():
    """Test that task timeout can be started."""
    from graph.timeout_manager import TimeoutManager
    
    manager = TimeoutManager()
    
    callback_called = []
    def callback(timeout_key, timeout_info):
        callback_called.append(timeout_key)
    
    manager.start_task_timeout("task_1", callback)
    
    active_timeouts = manager.get_active_timeouts("task_1")
    assert len(active_timeouts) == 1
    assert active_timeouts[0]["type"] == "task"


def test_start_watchdog_timeout():
    """Test that watchdog timeout can be started."""
    from graph.timeout_manager import TimeoutManager
    
    manager = TimeoutManager()
    
    callback_called = []
    def callback(timeout_key, timeout_info):
        callback_called.append(timeout_key)
    
    manager.start_watchdog_timeout("task_1", callback)
    
    active_timeouts = manager.get_active_timeouts("task_1")
    assert len(active_timeouts) == 1
    assert active_timeouts[0]["type"] == "watchdog"


def test_cancel_timeout():
    """Test that timeout can be cancelled."""
    from graph.timeout_manager import TimeoutManager
    
    manager = TimeoutManager()
    
    callback_called = []
    def callback(timeout_key, timeout_info):
        callback_called.append(timeout_key)
    
    manager.start_operation_timeout("task_1", "op_1", callback)
    
    cancelled = manager.cancel_timeout("task_1:op_1")
    
    assert cancelled is True
    active_timeouts = manager.get_active_timeouts("task_1")
    assert len(active_timeouts) == 0


def test_cancel_all_timeouts_for_task():
    """Test that all timeouts for a task can be cancelled."""
    from graph.timeout_manager import TimeoutManager
    
    manager = TimeoutManager()
    
    callback_called = []
    def callback(timeout_key, timeout_info):
        callback_called.append(timeout_key)
    
    manager.start_operation_timeout("task_1", "op_1", callback)
    manager.start_step_timeout("task_1", "step_1", callback)
    manager.start_task_timeout("task_1", callback)
    
    cancelled_count = manager.cancel_all_timeouts_for_task("task_1")
    
    assert cancelled_count == 3
    active_timeouts = manager.get_active_timeouts("task_1")
    assert len(active_timeouts) == 0


# ─── CANCELLATION SERVICE TESTS ─────────────────────────────────────────────

def test_cancellation_service_init():
    """Test that CancellationService can be initialized."""
    from graph.cancellation import CancellationService
    
    service = CancellationService()
    assert service is not None


def test_request_cancellation():
    """Test that cancellation can be requested."""
    from graph.cancellation import CancellationService
    from migration.domain_contracts import CancellationReason
    
    service = CancellationService()
    
    cancellation = service.request_cancellation(
        task_id="task_1",
        reason=CancellationReason.USER_REQUESTED,
        requested_by="user"
    )
    
    assert cancellation is not None
    assert cancellation.task_id == "task_1"
    assert cancellation.reason == CancellationReason.USER_REQUESTED


def test_cancel_workflow():
    """Test that workflow can be cancelled."""
    from graph.cancellation import CancellationService
    from migration.graph_state import OperonixState
    from migration.domain_contracts import TaskRequest, TaskSource, CancellationReason
    
    service = CancellationService()
    
    task = TaskRequest(user_input="Open Firefox", source=TaskSource.VOICE)
    state = OperonixState(task=task)
    
    cancellation = service.request_cancellation(
        task_id=task.task_id,
        reason=CancellationReason.USER_REQUESTED,
        requested_by="user"
    )
    
    result = service.cancel_workflow(state, cancellation)
    
    assert result["state"].cancelled is True
    assert result["state"].cancellation is not None


def test_handle_user_cancellation():
    """Test that user cancellation can be handled."""
    from graph.cancellation import CancellationService
    from migration.graph_state import OperonixState
    from migration.domain_contracts import TaskRequest, TaskSource
    
    service = CancellationService()
    
    task = TaskRequest(user_input="Open Firefox", source=TaskSource.VOICE)
    state = OperonixState(task=task)
    
    result = service.handle_user_cancellation(state)
    
    assert result["state"].cancelled is True
    assert result["state"].cancellation.reason.value == "user_requested"


def test_handle_timeout_cancellation():
    """Test that timeout cancellation can be handled."""
    from graph.cancellation import CancellationService
    from migration.graph_state import OperonixState
    from migration.domain_contracts import TaskRequest, TaskSource
    
    service = CancellationService()
    
    task = TaskRequest(user_input="Open Firefox", source=TaskSource.VOICE)
    state = OperonixState(task=task)
    
    result = service.handle_timeout_cancellation(state, "operation")
    
    assert result["state"].cancelled is True
    assert result["state"].cancellation.reason.value == "timeout"


def test_handle_resource_contention():
    """Test that resource contention cancellation can be handled."""
    from graph.cancellation import CancellationService
    from migration.graph_state import OperonixState
    from migration.domain_contracts import TaskRequest, TaskSource
    
    service = CancellationService()
    
    task = TaskRequest(user_input="Open Firefox", source=TaskSource.VOICE)
    state = OperonixState(task=task)
    
    result = service.handle_resource_contention(state, "keyboard")
    
    assert result["state"].cancelled is True
    assert result["state"].cancellation.reason.value == "resource_contention"


def test_abort_semantics_determination():
    """Test that abort semantics are determined correctly."""
    from graph.cancellation import CancellationService
    from migration.domain_contracts import CancellationReason, AbortSemantics
    
    service = CancellationService()
    
    assert service._determine_abort_semantics(CancellationReason.USER_REQUESTED) == AbortSemantics.GRACEFUL
    assert service._determine_abort_semantics(CancellationReason.TIMEOUT) == AbortSemantics.SAFE
    assert service._determine_abort_semantics(CancellationReason.SAFE_ABORT) == AbortSemantics.SAFE
    assert service._determine_abort_semantics(CancellationReason.RESOURCE_CONTENTION) == AbortSemantics.GRACEFUL
    assert service._determine_abort_semantics(CancellationReason.SYSTEM_ERROR) == AbortSemantics.IMMEDIATE


# ─── ABORT SEMANTICS TESTS ───────────────────────────────────────────────────

def test_abort_decision_domain_object():
    """Test that AbortDecision is a valid domain object."""
    from migration.domain_contracts import AbortDecision, AbortSemantics
    
    abort = AbortDecision(
        task_id="test_task",
        semantics=AbortSemantics.GRACEFUL,
        reason="User requested cancellation"
    )
    
    assert abort.task_id == "test_task"
    assert abort.semantics == AbortSemantics.GRACEFUL
    assert abort.abort_id is not None
    assert abort.decision_timestamp is not None


def test_abort_semantics_enum():
    """Test that AbortSemantics enum has all required values."""
    from migration.domain_contracts import AbortSemantics
    
    assert AbortSemantics.IMMEDIATE.value == "immediate"
    assert AbortSemantics.GRACEFUL.value == "graceful"
    assert AbortSemantics.SAFE.value == "safe"


# ─── RESOURCE OWNERSHIP TESTS ───────────────────────────────────────────────

def test_resource_ownership_domain_object():
    """Test that ResourceOwnership is a valid domain object."""
    from migration.domain_contracts import ResourceOwnership, ResourceType
    
    ownership = ResourceOwnership(
        task_id="test_task",
        resource_type=ResourceType.KEYBOARD
    )
    
    assert ownership.task_id == "test_task"
    assert ownership.resource_type == ResourceType.KEYBOARD
    assert ownership.ownership_id is not None
    assert ownership.acquired_at is not None


def test_resource_type_enum():
    """Test that ResourceType enum has all required values."""
    from migration.domain_contracts import ResourceType
    
    assert ResourceType.KEYBOARD.value == "keyboard"
    assert ResourceType.MOUSE.value == "mouse"
    assert ResourceType.ACTIVE_WINDOW.value == "active_window"
    assert ResourceType.FOCUS.value == "focus"
    assert ResourceType.SCREEN.value == "screen"
    assert ResourceType.AUDIO.value == "audio"
    assert ResourceType.NETWORK.value == "network"
    assert ResourceType.FILESYSTEM.value == "filesystem"


# ─── RESOURCE MANAGER TESTS ─────────────────────────────────────────────────

def test_resource_manager_init():
    """Test that ResourceManager can be initialized."""
    from graph.resource_manager import ResourceManager
    
    manager = ResourceManager()
    assert manager is not None


def test_acquire_resource():
    """Test that resource can be acquired."""
    from graph.resource_manager import ResourceManager
    from migration.domain_contracts import ResourceType
    
    manager = ResourceManager()
    
    ownership = manager.acquire_resource(
        task_id="task_1",
        resource_type=ResourceType.KEYBOARD
    )
    
    assert ownership is not None
    assert ownership.task_id == "task_1"
    assert ownership.resource_type == ResourceType.KEYBOARD


def test_acquire_resource_with_identifier():
    """Test that resource can be acquired with identifier."""
    from graph.resource_manager import ResourceManager
    from migration.domain_contracts import ResourceType
    
    manager = ResourceManager()
    
    ownership = manager.acquire_resource(
        task_id="task_1",
        resource_type=ResourceType.ACTIVE_WINDOW,
        resource_identifier="Firefox"
    )
    
    assert ownership is not None
    assert ownership.resource_identifier == "Firefox"


def test_acquire_resource_with_expiration():
    """Test that resource can be acquired with expiration."""
    from graph.resource_manager import ResourceManager
    from migration.domain_contracts import ResourceType
    
    manager = ResourceManager()
    
    ownership = manager.acquire_resource(
        task_id="task_1",
        resource_type=ResourceType.KEYBOARD,
        expires_in_seconds=60
    )
    
    assert ownership is not None
    assert ownership.expires_at is not None


def test_acquire_locked_resource():
    """Test that locked resource cannot be acquired by another task."""
    from graph.resource_manager import ResourceManager
    from migration.domain_contracts import ResourceType
    
    manager = ResourceManager()
    
    # Task 1 acquires keyboard
    manager.acquire_resource(task_id="task_1", resource_type=ResourceType.KEYBOARD)
    
    # Task 2 tries to acquire keyboard
    ownership = manager.acquire_resource(task_id="task_2", resource_type=ResourceType.KEYBOARD)
    
    assert ownership is None  # Cannot acquire locked resource


def test_release_resource():
    """Test that resource can be released."""
    from graph.resource_manager import ResourceManager
    from migration.domain_contracts import ResourceType
    
    manager = ResourceManager()
    
    ownership = manager.acquire_resource(
        task_id="task_1",
        resource_type=ResourceType.KEYBOARD
    )
    
    released = manager.release_resource(ownership.ownership_id)
    
    assert released is True


def test_release_all_resources_for_task():
    """Test that all resources for a task can be released."""
    from graph.resource_manager import ResourceManager
    from migration.domain_contracts import ResourceType
    
    manager = ResourceManager()
    
    manager.acquire_resource(task_id="task_1", resource_type=ResourceType.KEYBOARD)
    manager.acquire_resource(task_id="task_1", resource_type=ResourceType.MOUSE)
    manager.acquire_resource(task_id="task_1", resource_type=ResourceType.FOCUS)
    
    released_count = manager.release_all_resources_for_task("task_1")
    
    assert released_count == 3


def test_check_resource_availability():
    """Test that resource availability can be checked."""
    from graph.resource_manager import ResourceManager
    from migration.domain_contracts import ResourceType
    
    manager = ResourceManager()
    
    # Resource is available initially
    available = manager.check_resource_availability(ResourceType.KEYBOARD, "task_1")
    assert available is True
    
    # Acquire resource
    manager.acquire_resource(task_id="task_1", resource_type=ResourceType.KEYBOARD)
    
    # Resource is available to owner
    available = manager.check_resource_availability(ResourceType.KEYBOARD, "task_1")
    assert available is True
    
    # Resource is not available to other task
    available = manager.check_resource_availability(ResourceType.KEYBOARD, "task_2")
    assert available is False


def test_get_active_ownerships():
    """Test that active ownerships can be retrieved."""
    from graph.resource_manager import ResourceManager
    from migration.domain_contracts import ResourceType
    
    manager = ResourceManager()
    
    manager.acquire_resource(task_id="task_1", resource_type=ResourceType.KEYBOARD)
    manager.acquire_resource(task_id="task_1", resource_type=ResourceType.MOUSE)
    manager.acquire_resource(task_id="task_2", resource_type=ResourceType.KEYBOARD)
    
    # Get all ownerships
    all_ownerships = manager.get_active_ownerships()
    assert len(all_ownerships) == 3
    
    # Get ownerships for task_1
    task_1_ownerships = manager.get_active_ownerships("task_1")
    assert len(task_1_ownerships) == 2


def test_get_resource_locks():
    """Test that resource locks can be retrieved."""
    from graph.resource_manager import ResourceManager
    from migration.domain_contracts import ResourceType
    
    manager = ResourceManager()
    
    manager.acquire_resource(task_id="task_1", resource_type=ResourceType.KEYBOARD)
    manager.acquire_resource(task_id="task_1", resource_type=ResourceType.MOUSE)
    
    locks = manager.get_resource_locks()
    
    assert ResourceType.KEYBOARD in locks
    assert ResourceType.MOUSE in locks
    assert locks[ResourceType.KEYBOARD] == "task_1"
    assert locks[ResourceType.MOUSE] == "task_1"


# ─── GRAPH CONDITIONAL ROUTING TESTS ─────────────────────────────────────────

def test_graph_conditional_routing_cancelled():
    """Test that cancelled state routes to cancel node."""
    from graph.graph import should_recover_or_cancel
    from migration.graph_state import OperonixState
    from migration.domain_contracts import TaskRequest, TaskSource
    
    task = TaskRequest(user_input="Open Firefox", source=TaskSource.VOICE)
    state = OperonixState(task=task)
    state.cancelled = True
    
    result = should_recover_or_cancel(state)
    assert result == "cancel"


def test_graph_conditional_routing_not_cancelled_verified():
    """Test that non-cancelled verified state routes to finalize."""
    from graph.graph import should_recover_or_cancel
    from migration.graph_state import OperonixState
    from migration.domain_contracts import TaskRequest, TaskSource, VerificationResult, ContextSnapshot
    
    task = TaskRequest(user_input="Open Firefox", source=TaskSource.VOICE)
    state = OperonixState(task=task)
    state.cancelled = False
    state.verification = VerificationResult(
        status="VERIFIED",
        observed_context=ContextSnapshot(),
        expected_state={},
        actual_state={}
    )
    
    result = should_recover_or_cancel(state)
    assert result == "finalize"


def test_recovery_target_with_cancellation():
    """Test that recovery target routes to finalize if cancelled."""
    from graph.graph import get_recovery_target
    from migration.graph_state import OperonixState
    from migration.domain_contracts import TaskRequest, TaskSource
    
    task = TaskRequest(user_input="Open Firefox", source=TaskSource.VOICE)
    state = OperonixState(task=task)
    state.cancelled = True
    
    result = get_recovery_target(state)
    assert result == "finalize"
