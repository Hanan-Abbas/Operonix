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
from typing import Dict, Any

from migration.graph_state import OperonixState

logger = logging.getLogger("Graph.Observe")


def observe_node(state: OperonixState) -> Dict[str, Any]:
    """Observe node: Gathers context about current environment.
    
    This node calls context services to understand the current state:
    - WindowDetector
    - AppClassifier
    - StateExtractor
    - FocusTracker
    - ContextValidator
    
    In Phase 1 foundation, this is a stub that logs the intent.
    Later phases will integrate actual context services.
    
    Args:
        state: Current OperonixState
        
    Returns:
        Dict with updated state
    """
    logger.info(f"OBSERVE: Gathering context for task {state.task.task_id}")
    
    state.add_history_event("observe_started", {
        "task_id": state.task.task_id
    })
    
    # In Phase 1 foundation, we don't actually call context services
    # We just log that observation would happen
    # Later phases will integrate:
    # - from context.window_detector import WindowDetector
    # - from context.app_classifier import AppClassifier
    # - from context.state_extractor import StateExtractor
    # - from context.focus_tracker import FocusTracker
    # - from context.context_validator import ContextValidator
    
    logger.info("OBSERVE: Context services integration deferred to later phases")
    
    state.add_history_event("observe_completed", {
        "task_id": state.task.task_id,
        "note": "Context services integration deferred to later phases"
    })
    
    state.update_timestamp()
    
    return {"state": state}
