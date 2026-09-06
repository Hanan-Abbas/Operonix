"""
Safety Check Node — Operonix Graph
────────────────────────────────

Safety check node: Safety authorization and validation.
Per migration plan §4.2, node 8:
"safety_check — calls safety/validator, permission_guard, risk_rules. Writes
state.safety. May require confirmation."
"""
from __future__ import annotations

import logging
from typing import Dict, Any

from migration.graph_state import OperonixState
from migration.domain_contracts import SafetyDecision, RiskLevel

logger = logging.getLogger("Graph.SafetyCheck")


def safety_check_node(state: OperonixState) -> Dict[str, Any]:
    """Safety check node: Validate and authorize execution.
    
    This node:
    - Calls safety validator to assess risk
    - Checks permissions via permission guard
    - Applies risk rules
    - May require user confirmation for risky actions
    - Creates SafetyDecision with authorization status
    
    Phase 7 enhancement: Trigger confirmation flow when confirmation_required is True.
    
    In Phase 4-7, this is a stub that creates a placeholder SafetyDecision.
    Later phases will integrate with existing safety/ module.
    
    Args:
        state: Current OperonixState
        
    Returns:
        Dict with updated state including safety decision
    """
    logger.info(f"SAFETY_CHECK: Performing safety check for task {state.task.task_id}")
    
    state.add_history_event("safety_check_started", {
        "task_id": state.task.task_id,
        "method": state.routing.selected_candidate.method_type if state.routing else None
    })
    
    # In Phase 4-7, we create a placeholder safety decision
    # Later phases will integrate with:
    # - from safety.validator import Validator
    # - from safety.permission_guard import PermissionGuard
    # - from safety.risk_rules import RiskRules
    # - from safety.confirmation import Confirmation
    
    logger.info("SAFETY_CHECK: Safety integration deferred to later phases (STUB)")
    
    # Create placeholder safety decision
    # For Phase 7, we can simulate confirmation_required for testing
    # In a real implementation, this would be determined by risk rules
    safety_decision = SafetyDecision(
        risk_level=RiskLevel.LOW,
        validation_status="APPROVED",
        permission_status="GRANTED",
        confirmation_required=False,  # Can be set to True to test confirmation flow
        safety_checks_performed=["placeholder_check"]
    )
    
    state.safety = safety_decision
    
    state.add_history_event("safety_check_completed", {
        "task_id": state.task.task_id,
        "risk_level": safety_decision.risk_level.value,
        "validation_status": safety_decision.validation_status,
        "confirmation_required": safety_decision.confirmation_required
    })
    
    state.update_timestamp()
    
    return {"state": state}
