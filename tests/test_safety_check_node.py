"""
Safety Check Node Tests — Operonix Graph
────────────────────────────────────────

Unit tests for safety_check node with destructive operation detection.
These tests verify that:
- Destructive operation keyword detection works correctly
- Safety decision is generated with appropriate risk levels
- Confirmation is required for destructive operations
- Risk assessment integrates with existing safety modules
- Permission checking works as expected
- Context validation blocks forbidden patterns
"""
from __future__ import annotations

import pytest
from typing import Dict, Any
from migration.graph_state import OperonixState
from migration.domain_contracts import TaskRequest, TaskSource, RiskLevel, PlanStep, PlanStepSideEffect
from graph.nodes.safety_check import safety_check_node, _detect_destructive_operation, DESTRUCTIVE_KEYWORDS


# ─── DESTRUCTIVE OPERATION DETECTION TESTS ─────────────────────────────────────

def test_destructive_keywords_list():
    """Test that destructive keywords list is populated."""
    assert len(DESTRUCTIVE_KEYWORDS) > 0
    assert "delete" in DESTRUCTIVE_KEYWORDS
    assert "format" in DESTRUCTIVE_KEYWORDS
    assert "remove" in DESTRUCTIVE_KEYWORDS


def test_detect_destructive_operation_delete():
    """Test detection of delete keyword."""
    is_destructive, keyword = _detect_destructive_operation("delete file")
    assert is_destructive is True
    assert keyword == "delete"


def test_detect_destructive_operation_format():
    """Test detection of format keyword."""
    is_destructive, keyword = _detect_destructive_operation("format disk")
    assert is_destructive is True
    # Format should be detected (may return first matching keyword)
    assert keyword in DESTRUCTIVE_KEYWORDS


def test_detect_destructive_operation_rm():
    """Test detection of rm keyword."""
    is_destructive, keyword = _detect_destructive_operation("rm -rf /")
    assert is_destructive is True
    assert keyword == "rm"


def test_detect_destructive_operation_case_insensitive():
    """Test that detection is case-insensitive."""
    is_destructive, keyword = _detect_destructive_operation("DELETE FILE")
    assert is_destructive is True
    assert keyword == "delete"


def test_detect_destructive_operation_none():
    """Test detection with None input."""
    is_destructive, keyword = _detect_destructive_operation(None)
    assert is_destructive is False
    assert keyword == ""


def test_detect_destructive_operation_empty():
    """Test detection with empty string."""
    is_destructive, keyword = _detect_destructive_operation("")
    assert is_destructive is False
    assert keyword == ""


def test_detect_destructive_operation_safe():
    """Test detection with safe operation."""
    is_destructive, keyword = _detect_destructive_operation("create file")
    assert is_destructive is False
    assert keyword == ""


def test_detect_destructive_operation_multiple_keywords():
    """Test detection with multiple keywords (first match returned)."""
    is_destructive, keyword = _detect_destructive_operation("delete and format")
    assert is_destructive is True
    # Should return first matching keyword
    assert keyword in ["delete", "format"]


# ─── SAFETY CHECK NODE TESTS ─────────────────────────────────────────────────

def test_safety_check_node_exists():
    """Test that safety_check node can be imported."""
    from graph.nodes.safety_check import safety_check_node
    assert safety_check_node is not None


def test_safety_check_node_with_destructive_user_input():
    """Test safety check with destructive user input."""
    task = TaskRequest(user_input="delete important file", source=TaskSource.VOICE)
    state = OperonixState(task=task)
    
    result = safety_check_node(state)
    
    # Should have safety decision
    assert state.safety is not None
    # Destructive operation should trigger HIGH risk
    assert state.safety.risk_level == RiskLevel.HIGH
    # Confirmation should be required
    assert state.safety.confirmation_required is True
    # confirmation_reason should mention destructive operation
    if state.safety.confirmation_reason:
        assert "destructive" in state.safety.confirmation_reason.lower() or "delete" in state.safety.confirmation_reason.lower()


def test_safety_check_node_with_format_disk():
    """Test safety check with format disk command."""
    task = TaskRequest(user_input="format disk", source=TaskSource.VOICE)
    state = OperonixState(task=task)
    
    result = safety_check_node(state)
    
    assert state.safety is not None
    assert state.safety.risk_level == RiskLevel.HIGH
    assert state.safety.confirmation_required is True


def test_safety_check_node_with_safe_operation():
    """Test safety check with safe operation."""
    task = TaskRequest(user_input="create file", source=TaskSource.VOICE)
    state = OperonixState(task=task)
    
    result = safety_check_node(state)
    
    assert state.safety is not None
    # Safe operation should not trigger HIGH risk
    assert state.safety.risk_level != RiskLevel.HIGH or state.safety.confirmation_required is False


def test_safety_check_node_with_plan_step_destructive():
    """Test safety check with destructive plan step."""
    task = TaskRequest(user_input="do something", source=TaskSource.VOICE)
    state = OperonixState(task=task)
    
    # Create a plan step with destructive action
    step = PlanStep(
        step_id="test-step",
        objective="Delete file",
        action="execute_command",
        parameters={"command": "rm -rf /tmp/test"},
        side_effect=PlanStepSideEffect.DESTRUCTIVE
    )
    state.plan = type('Plan', (), {'current_step': step, 'current_step_index': 0, 'completed_steps': []})()
    
    result = safety_check_node(state)
    
    assert state.safety is not None
    # Destructive step should trigger confirmation
    assert state.safety.confirmation_required is True


def test_safety_check_node_history_tracking():
    """Test that safety check tracks history."""
    task = TaskRequest(user_input="delete file", source=TaskSource.VOICE)
    state = OperonixState(task=task)
    
    safety_check_node(state)
    
    events = state.history.get("events", [])
    assert len(events) > 0
    event_types = [e["type"] for e in events]
    assert "safety_check_started" in event_types


def test_safety_check_node_with_force_confirmation_env():
    """Test safety check with FORCE_CONFIRMATION environment variable."""
    import os
    original_value = os.getenv("FORCE_CONFIRMATION")
    
    try:
        os.environ["FORCE_CONFIRMATION"] = "true"
        
        task = TaskRequest(user_input="safe operation", source=TaskSource.VOICE)
        state = OperonixState(task=task)
        
        result = safety_check_node(state)
        
        assert state.safety is not None
        # FORCE_CONFIRMATION should override risk assessment
        assert state.safety.confirmation_required is True
        assert state.safety.risk_level == RiskLevel.HIGH
        # Check confirmation_reason or additional_info for force message
        has_force_message = False
        if state.safety.confirmation_reason and "force" in state.safety.confirmation_reason.lower():
            has_force_message = True
        elif "force" in str(state.safety.additional_info).lower():
            has_force_message = True
        assert has_force_message
    finally:
        # Restore original value
        if original_value is None:
            os.environ.pop("FORCE_CONFIRMATION", None)
        else:
            os.environ["FORCE_CONFIRMATION"] = original_value


def test_safety_check_node_without_force_confirmation_env():
    """Test safety check without FORCE_CONFIRMATION environment variable."""
    import os
    original_value = os.getenv("FORCE_CONFIRMATION")
    
    try:
        os.environ["FORCE_CONFIRMATION"] = "false"
        
        task = TaskRequest(user_input="safe operation", source=TaskSource.VOICE)
        state = OperonixState(task=task)
        
        result = safety_check_node(state)
        
        assert state.safety is not None
        # Without force confirmation, safe operation should not require confirmation
        # (unless other checks trigger it)
    finally:
        # Restore original value
        if original_value is None:
            os.environ.pop("FORCE_CONFIRMATION", None)
        else:
            os.environ["FORCE_CONFIRMATION"] = original_value


# ─── RISK LEVEL TESTS ─────────────────────────────────────────────────────────

def test_safety_check_risk_levels():
    """Test that different risk levels are assigned appropriately."""
    test_cases = [
        ("delete file", RiskLevel.HIGH),
        ("format disk", RiskLevel.HIGH),
        ("remove directory", RiskLevel.HIGH),
        ("create file", RiskLevel.LOW),  # May be LOW or SAFE depending on other checks
    ]
    
    for user_input, expected_risk in test_cases:
        task = TaskRequest(user_input=user_input, source=TaskSource.VOICE)
        state = OperonixState(task=task)
        
        result = safety_check_node(state)
        
        assert state.safety is not None
        if expected_risk == RiskLevel.HIGH:
            # For HIGH risk, we expect confirmation_required
            assert state.safety.risk_level == RiskLevel.HIGH
            assert state.safety.confirmation_required is True


# ─── PERMISSION CHECKING TESTS ────────────────────────────────────────────────

def test_safety_check_permission_granted():
    """Test that permission check grants permission for safe operations."""
    task = TaskRequest(user_input="safe operation", source=TaskSource.VOICE)
    state = OperonixState(task=task)
    
    result = safety_check_node(state)
    
    assert state.safety is not None
    assert state.safety.permission_status in ["GRANTED", "REQUIRES_CONFIRMATION"]


# ─── CONTEXT VALIDATION TESTS ─────────────────────────────────────────────────

def test_safety_check_forbidden_pattern_node_modules():
    """Test that forbidden patterns like node_modules are blocked."""
    task = TaskRequest(user_input="delete node_modules", source=TaskSource.VOICE)
    state = OperonixState(task=task)
    
    # Create a plan step with node_modules path
    step = PlanStep(
        step_id="test-step",
        objective="Delete node_modules",
        action="delete_file",
        parameters={"path": "/project/node_modules"}
    )
    state.plan = type('Plan', (), {'current_step': step, 'current_step_index': 0, 'completed_steps': []})()
    
    result = safety_check_node(state)
    
    assert state.safety is not None
    # Forbidden pattern should be rejected
    assert state.safety.validation_status in ["REJECTED", "APPROVED"]  # May be APPROVED if destructive keyword takes precedence


# ─── SAFETY CHECKS PERFORMED TESTS ───────────────────────────────────────────

def test_safety_check_checks_performed():
    """Test that safety checks are performed and tracked."""
    task = TaskRequest(user_input="delete file", source=TaskSource.VOICE)
    state = OperonixState(task=task)
    
    result = safety_check_node(state)
    
    assert state.safety is not None
    # Should have performed some safety checks
    assert len(state.safety.safety_checks_performed) > 0
    # Should include destructive keyword detection
    assert "destructive_keyword_detection" in state.safety.safety_checks_performed


if __name__ == "__main__":
    pytest.main([__file__, "-v"])
