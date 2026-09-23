"""
Observe Node — Operonix Graph
────────────────────────────

Observe node: Gathers context about current environment.
Per migration plan §4.2, node 2:
"gather_context / observe — calls WindowDetector, AppClassifier, StateExtractor,
 FocusTracker, ContextValidator; writes state.context"
"""
from __future__ import annotations

import logging
import asyncio
from typing import Dict, Any

from migration.graph_state import OperonixState
from migration.domain_contracts import ContextSnapshot
from graph.trace_collector import get_trace_collector

logger = logging.getLogger("Graph.Observe")


def observe_node(state: OperonixState) -> Dict[str, Any]:
    """Observe node: Gathers context about current environment.
    
    This node calls context services to understand the current state:
    - WindowDetector
    - AppClassifier
    - StateExtractor
    - FocusTracker
    - ContextValidator
    
    Phase 6 enhancement: Check postconditions to determine if operation already happened.
    This is for safe re-execution - if a non-idempotent operation failed but may have
    already succeeded, we should check before retrying.
    
    Per migration plan Phase 6:
    ```
    failure
      ↓
    observe
      ↓
    check postcondition
      ↓
    already happened?
      ├── yes → verify / continue
      └── no  → retry / recover
    ```
    
    Phase 9/10 enhancement: Integrate actual context services for real observation.
    
    Args:
        state: Current OperonixState
        
    Returns:
        Dict with updated state
    """
    logger.info(f"OBSERVE: Gathering context for task {state.task.task_id}")
    
    state.add_history_event("observe_started", {
        "task_id": state.task.task_id
    })
    
    # Phase 6: Check if this is a recovery observation (checking if operation already happened)
    is_recovery_observation = state.recovery is not None and state.recovery.recovery_strategy.value == "observe"
    
    if is_recovery_observation:
        # Check postconditions to determine if operation already happened
        postcondition_check = _check_postconditions(state)
        
        # Store postcondition check result in state for recovery decision
        state.context = state.context or {}
        state.context["postcondition_check"] = postcondition_check
        
        logger.info(f"OBSERVE: Postcondition check result: {postcondition_check}")
    else:
        # Initial observation (not recovery) - integrate actual context services
        logger.info("OBSERVE: Initial observation with actual context services")
        
        # Integrate with actual context services
        context_snapshot = _gather_context_snapshot(state)
        
        # Store context snapshot in state
        state.context = context_snapshot
        
        logger.info(f"OBSERVE: Context snapshot gathered: window={context_snapshot.get('window_title')}, app={context_snapshot.get('app_name')}")
    
    # Phase 9: Collect trace event for observation
    trace_collector = get_trace_collector()
    trace_collector.collect_observation(
        task_id=state.task.task_id,
        observation_data={
            "is_recovery_observation": is_recovery_observation,
            "postcondition_check": state.context.get("postcondition_check") if is_recovery_observation else None,
            "window_title": state.context.get("window_title") if not is_recovery_observation else None,
            "app_name": state.context.get("app_name") if not is_recovery_observation else None,
            "cwd": state.context.get("cwd") if not is_recovery_observation else None,
            "validation": state.context.get("validation") if not is_recovery_observation else None
        }
    )
    
    state.add_history_event("observe_completed", {
        "task_id": state.task.task_id,
        "is_recovery_observation": is_recovery_observation,
        "postcondition_check": state.context.get("postcondition_check") if is_recovery_observation else None,
        "validation": state.context.get("validation") if not is_recovery_observation else None
    })
    
    state.update_timestamp()
    
    return {"context": state.context}


def _convert_confidence_to_float(confidence_str: str) -> float:
    """Convert string confidence values to float for Pydantic validation.
    
    Args:
        confidence_str: String confidence value ("high", "medium", "low")
        
    Returns:
        Float confidence value (0.0-1.0)
    """
    confidence_map = {
        "high": 0.9,
        "medium": 0.6,
        "low": 0.3
    }
    return confidence_map.get(str(confidence_str).lower(), 0.5)

def _gather_context_snapshot(state: OperonixState) -> Dict[str, Any]:
    """Gather context snapshot using actual context services.
    
    Phase 11 enhancement: Full integration with all context services.
    This integrates with:
    - WindowDetector (for window title, app name, cwd)
    - AppClassifier (for app type classification)
    - StateExtractor (for deep UI state)
    - FocusTracker (for focus tracking)
    - ContextValidator (for context validation)
    
    Args:
        state: Current OperonixState
        
    Returns:
        Dict with context snapshot data
    """
    context_data = {
        "window_title": "Unknown",
        "app_name": "Unknown",
        "app_type": "unknown",
        "cwd": None,
        "window_pid": None,
        "confidence": 0.0,
        "sub_context": None,
        "state": {},
        "focus": {},
        "validation": {}
    }
    
    try:
        # Try to get context from WindowDetector
        try:
            from context.window_detector import window_detector
            
            if hasattr(window_detector, '_last_external_snapshot') and window_detector._last_external_snapshot:
                snapshot = window_detector._last_external_snapshot
                context_data["window_title"] = snapshot.get("window_title", "Unknown")
                context_data["app_name"] = snapshot.get("app_name", "Unknown")
                context_data["app_type"] = snapshot.get("app_type", "unknown")
                context_data["cwd"] = snapshot.get("cwd")
                context_data["window_pid"] = snapshot.get("window_pid")
                # Convert string confidence to float for Pydantic validation
                confidence_value = snapshot.get("confidence", 0.0)
                context_data["confidence"] = _convert_confidence_to_float(confidence_value)
                context_data["sub_context"] = snapshot.get("sub_context")
                
                logger.info(f"Context snapshot from WindowDetector: {context_data['window_title']}")
            else:
                logger.warning("WindowDetector has no snapshot available")
        except ImportError:
            logger.warning("Could not import WindowDetector")
        except Exception as e:
            logger.error(f"Error getting context from WindowDetector: {e}")
        
        # Phase 11: Try to get app classification from AppClassifier
        try:
            from context.app_classifier import app_classifier
            
            if context_data.get("window_title"):
                classification = app_classifier.classify_app(
                    context_data["window_title"],
                    context_data.get("app_name")
                )
                if classification:
                    context_data["app_type"] = classification.get("app_type", context_data["app_type"])
                    context_data["app_category"] = classification.get("category")
                    # Convert string confidence to float for Pydantic validation
                    app_confidence = classification.get("confidence", 0.0)
                    context_data["app_confidence"] = _convert_confidence_to_float(app_confidence)
                    
                    logger.debug(f"App classification: {context_data['app_type']}")
        except ImportError:
            logger.warning("Could not import AppClassifier")
        except Exception as e:
            logger.error(f"Error getting app classification: {e}")
        
        # Try to get deep state from StateExtractor
        try:
            from context.state_extractor import state_extractor
            
            if context_data.get("window_title"):
                heuristics = state_extractor._get_heuristics(
                    context_data["window_title"],
                    context_data.get("app_type")
                )
                context_data["state"].update(heuristics)
                
                logger.debug(f"State heuristics: {heuristics}")
        except ImportError:
            logger.warning("Could not import StateExtractor")
        except Exception as e:
            logger.error(f"Error getting state from StateExtractor: {e}")
        
        # Phase 11: Try to get focus information from FocusTracker
        try:
            from context.focus_tracker import focus_tracker
            
            focus_info = focus_tracker.get_current_focus()
            if focus_info:
                context_data["focus"] = {
                    "focused_element": focus_info.get("element"),
                    "focused_window": focus_info.get("window"),
                    "focus_timestamp": focus_info.get("timestamp")
                }
                
                logger.debug(f"Focus info: {context_data['focus']}")
        except ImportError:
            logger.warning("Could not import FocusTracker")
        except Exception as e:
            logger.error(f"Error getting focus info: {e}")
        
        # Phase 11: Try to validate context with ContextValidator
        try:
            from context.context_validator import context_validator
            
            # Simplified validation: use synchronous fallback if async not available
            # This avoids complex event loop management
            try:
                # Try to use async validation if available and we have an event loop
                import asyncio
                loop = asyncio.get_running_loop()
                
                # If we have a running loop, we can safely use asyncio.run in a thread
                # This is cleaner than creating/destroying event loops
                import concurrent.futures
                with concurrent.futures.ThreadPoolExecutor(max_workers=1) as executor:
                    future = executor.submit(
                        asyncio.run,
                        context_validator.validate_action_context(
                            state.task.user_input,
                            context_data
                        )
                    )
                    validation_result = future.result(timeout=5.0)  # 5 second timeout
                    
                is_valid, reason = validation_result
                context_data["validation"] = {
                    "is_valid": is_valid,
                    "reason": reason,
                    "validation_errors": [] if is_valid else [reason],
                    "validation_warnings": []
                }
                    
            except (RuntimeError, concurrent.futures.TimeoutError):
                # No event loop or timeout, use simplified synchronous validation
                logger.debug("Using simplified synchronous context validation")
                is_valid, reason = _simplified_context_validation(
                    state.task.user_input,
                    context_data
                )
                context_data["validation"] = {
                    "is_valid": is_valid,
                    "reason": reason,
                    "validation_errors": [] if is_valid else [reason],
                    "validation_warnings": []
                }
            
            # Log validation result
            if context_data["validation"]["is_valid"]:
                logger.info(f"Context validation passed: {context_data['validation']['reason']}")
            else:
                logger.warning(f"Context validation failed: {context_data['validation']['reason']}")
                
        except ImportError:
            logger.warning("Could not import ContextValidator, using simplified validation")
            # Fallback to simplified validation
            is_valid, reason = _simplified_context_validation(
                state.task.user_input,
                context_data
            )
            context_data["validation"] = {
                "is_valid": is_valid,
                "reason": reason,
                "validation_errors": [] if is_valid else [reason],
                "validation_warnings": []
            }
        except Exception as e:
            logger.error(f"Error validating context: {e}, using simplified validation")
            # Fallback to simplified validation on error
            is_valid, reason = _simplified_context_validation(
                state.task.user_input,
                context_data
            )
            context_data["validation"] = {
                "is_valid": is_valid,
                "reason": reason,
                "validation_errors": [] if is_valid else [reason],
                "validation_warnings": []
            }
        
    except Exception as e:
        logger.error(f"Error gathering context snapshot: {e}")
    
    return context_data


def _simplified_context_validation(user_input: str, context_data: Dict[str, Any]) -> tuple[bool, str]:
    """Simplified synchronous context validation fallback.
    
    This provides basic validation without complex async operations.
    
    Args:
        user_input: User input text
        context_data: Current context data
        
    Returns:
        Tuple of (is_valid, reason)
    """
    # Basic validation checks
    forbidden_patterns = [r"node_modules", r"\.env$", r"\.git"]
    import re
    import os
    
    # Check for forbidden patterns in user input
    for pattern in forbidden_patterns:
        if re.search(pattern, user_input, re.IGNORECASE):
            return False, f"Access to restricted pattern: {pattern}"
    
    # Check context data for issues
    if context_data.get("cwd"):
        cwd = context_data["cwd"]
        for pattern in forbidden_patterns:
            if re.search(pattern, cwd, re.IGNORECASE):
                return False, f"Current directory contains restricted pattern: {pattern}"
    
    # Check if we have valid context
    if not context_data.get("window_title") or context_data.get("window_title") == "Unknown":
        return False, "No valid window context available"
    
    return True, "Context validation passed"


def _check_postconditions(state: OperonixState) -> bool:
    """Check if postconditions are already met (operation already happened).
    
    This is used during recovery to determine if a failed operation may have
    already succeeded. If postconditions are met, we can continue instead of retrying.
    
    Phase 9/10 enhancement: Integrate actual context services for postcondition checking.
    
    Args:
        state: Current OperonixState
        
    Returns:
        True if postconditions are met (operation already happened), False otherwise
    """
    # Get current step from plan
    if not state.plan or state.plan.current_step_index >= len(state.plan.steps):
        return False  # No plan or invalid step, cannot determine
    
    current_step = state.plan.steps[state.plan.current_step_index]
    
    expected_outcome = current_step.expected_outcome if hasattr(current_step, 'expected_outcome') else current_step.objective
    
    # Basic postcondition check: if we have a verification result, check its status
    if state.verification and state.verification.status == "VERIFIED":
        logger.info(f"Postconditions already verified for step {current_step.step_id}")
        return True
    
    # Integrate actual context checking for postconditions
    # Gather current context to check if expected state is already present
    try:
        context_snapshot = _gather_context_snapshot(state)
        
        # Check postconditions based on step objective
        # This is a simplified implementation - a full implementation would parse
        # the objective and check specific conditions (file exists, window open, etc.)
        
        objective_lower = expected_outcome.lower()
        
        # Check for file existence
        if "file" in objective_lower and ("create" in objective_lower or "write" in objective_lower):
            # Extract file path from objective (simplified)
            import os
            import re
            
            # Try to find a path in the objective
            path_match = re.search(r'[~/]?[\w/\\]+[\w/\\]*\.\w+', expected_outcome)
            if path_match:
                file_path = path_match.group(0)
                if os.path.exists(file_path):
                    logger.info(f"Postcondition met: file {file_path} exists")
                    return True
        
        # Check for application/window
        if "open" in objective_lower and ("app" in objective_lower or "application" in objective_lower):
            # Check if the app is already in the current window
            if context_snapshot.get("app_name") and context_snapshot["app_name"].lower() in objective_lower:
                logger.info(f"Postcondition met: app {context_snapshot['app_name']} is already open")
                return True
        
        # Check for directory
        if "directory" in objective_lower or "folder" in objective_lower:
            import os
            import re
            
            path_match = re.search(r'[~/]?[\w/\\]+', expected_outcome)
            if path_match:
                dir_path = path_match.group(0)
                if os.path.isdir(dir_path):
                    logger.info(f"Postcondition met: directory {dir_path} exists")
                    return True
        
    except Exception as e:
        logger.error(f"Error checking postconditions with context: {e}")
    
    # Placeholder: assume postconditions not met (safe default)
    logger.info(f"Postcondition check for step {current_step.step_id}: postconditions not met")
    return False
