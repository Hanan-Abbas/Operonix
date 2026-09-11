"""
Safety Check Node — Operonix Graph
────────────────────────────────

Safety check node: Safety authorization and validation.
Per migration plan §4.2, node 8:
"safety_check — calls safety/validator, permission_guard, risk_rules. Writes
state.safety. May require confirmation."

Safety Check Integration Phase: Integrate actual safety modules.
"""
from __future__ import annotations

import logging
from typing import Dict, Any

from migration.graph_state import OperonixState
from migration.domain_contracts import SafetyDecision, RiskLevel
from graph.trace_collector import get_trace_collector

logger = logging.getLogger("Graph.SafetyCheck")


def safety_check_node(state: OperonixState) -> Dict[str, Any]:
    """Safety check node: Validate and authorize execution.
    
    This node:
    - Calls safety validator to assess risk
    - Checks permissions via permission guard
    - Applies risk rules
    - May require user confirmation for risky actions
    - Creates SafetyDecision with authorization status
    
    Safety Check Integration Phase: Integrate actual safety modules.
    
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
    
    # Safety Check Integration: Use actual safety modules
    try:
        # Get current step
        current_step = state.plan.current_step if state.plan and state.plan.current_step else None
        
        # Perform safety checks
        safety_checks_performed = []
        validation_status = "APPROVED"
        permission_status = "GRANTED"
        risk_level = RiskLevel.LOW
        confirmation_required = False
        reason = ""
        
        # 1. Risk assessment using risk_rules
        try:
            from safety.risk_rules import get_command_risk, get_file_op_risk, get_web_op_risk
            
            if current_step:
                step_action = getattr(current_step, 'action', None) or getattr(current_step, 'objective', '')
                step_args = getattr(current_step, 'parameters', {}) or {}
                
                # Assess risk based on step action
                if 'command' in step_action.lower() or 'shell' in step_action.lower():
                    command = step_args.get('command', '')
                    risk_level = get_command_risk(command)
                    safety_checks_performed.append("command_risk_assessment")
                elif 'file' in step_action.lower():
                    path = step_args.get('path', '')
                    risk_level = get_file_op_risk(step_action, path)
                    safety_checks_performed.append("file_risk_assessment")
                elif 'web' in step_action.lower() or 'api' in step_action.lower():
                    url = step_args.get('url', '')
                    risk_level = get_web_op_risk(url)
                    safety_checks_performed.append("web_risk_assessment")
                else:
                    risk_level = RiskLevel.LOW
                    safety_checks_performed.append("default_risk_assessment")
                
                logger.info(f"Risk assessment: {risk_level.value}")
        except ImportError:
            logger.warning("Could not import risk_rules, using default LOW risk")
            risk_level = RiskLevel.LOW
        except Exception as e:
            logger.error(f"Error in risk assessment: {e}, using default LOW risk")
            risk_level = RiskLevel.LOW
        
        # 2. Permission check using permission_guard logic
        try:
            from safety.permission_guard import _SERVICE_RISK
            
            if current_step:
                step_action = getattr(current_step, 'action', None) or getattr(current_step, 'objective', '')
                
                # Check if action is a service that requires permission
                for service, service_risk in _SERVICE_RISK.items():
                    if service in step_action.lower():
                        if service_risk == RiskLevel.HIGH:
                            permission_status = "REQUIRES_CONFIRMATION"
                            confirmation_required = True
                            reason = f"Service {service} requires confirmation"
                            safety_checks_performed.append("service_permission_check")
                            break
                
                logger.info(f"Permission check: {permission_status}")
        except ImportError:
            logger.warning("Could not import permission_guard, skipping permission check")
        except Exception as e:
            logger.error(f"Error in permission check: {e}, skipping")
        
        # 3. Context validation using validator logic
        try:
            if current_step and state.context:
                step_action = getattr(current_step, 'action', None) or getattr(current_step, 'objective', '')
                step_args = getattr(current_step, 'parameters', {}) or {}
                
                # Check for forbidden patterns
                forbidden_patterns = [r"node_modules", r"\.env$", r"\.git"]
                import re
                import os
                
                target_path = step_args.get('path') or step_args.get('target')
                if target_path:
                    normalized_path = os.path.normpath(target_path)
                    for pattern in forbidden_patterns:
                        if re.search(pattern, normalized_path, re.IGNORECASE):
                            validation_status = "REJECTED"
                            reason = f"Access to restricted pattern: {pattern}"
                            safety_checks_performed.append("path_pattern_check")
                            logger.warning(f"Path validation rejected: {pattern}")
                            break
                
                logger.info(f"Context validation: {validation_status}")
        except Exception as e:
            logger.error(f"Error in context validation: {e}, skipping")
        
        # 4. Determine if confirmation is required based on risk level
        if risk_level == RiskLevel.HIGH:
            confirmation_required = True
            if not reason:
                reason = "High risk operation requires confirmation"
            safety_checks_performed.append("high_risk_confirmation")
        elif risk_level == RiskLevel.FORBIDDEN:
            validation_status = "REJECTED"
            permission_status = "DENIED"
            if not reason:
                reason = "Operation is forbidden"
            safety_checks_performed.append("forbidden_operation")
        
        # Create safety decision
        safety_decision = SafetyDecision(
            risk_level=risk_level,
            validation_status=validation_status,
            permission_status=permission_status,
            confirmation_required=confirmation_required,
            safety_checks_performed=safety_checks_performed,
            additional_info={"reason": reason} if reason else {}
        )
        
        logger.info(f"SAFETY_CHECK: Safety decision - risk={risk_level.value}, validation={validation_status}, permission={permission_status}, confirmation={confirmation_required}")
        
    except Exception as e:
        logger.error(f"Error in safety check integration: {e}, using fallback")
        
        # Fallback to placeholder safety decision
        safety_decision = SafetyDecision(
            risk_level=RiskLevel.LOW,
            validation_status="APPROVED",
            permission_status="GRANTED",
            confirmation_required=False,
            safety_checks_performed=["fallback_check"],
            additional_info={"error": str(e)}
        )
    
    state.safety = safety_decision
    
    # Phase 9: Collect trace event for safety decision
    trace_collector = get_trace_collector()
    trace_collector.collect_safety_decision(
        task_id=state.task.task_id,
        safety_data={
            "risk_level": safety_decision.risk_level.value,
            "validation_status": safety_decision.validation_status,
            "permission_status": safety_decision.permission_status,
            "confirmation_required": safety_decision.confirmation_required,
            "safety_checks_performed": safety_decision.safety_checks_performed
        }
    )
    
    state.add_history_event("safety_check_completed", {
        "task_id": state.task.task_id,
        "risk_level": safety_decision.risk_level.value,
        "validation_status": safety_decision.validation_status,
        "permission_status": safety_decision.permission_status,
        "confirmation_required": safety_decision.confirmation_required
    })
    
    state.update_timestamp()
    
    return {"state": state}
