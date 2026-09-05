"""
LangGraph Topology — Operonix Migration Phase 1
──────────────────────────────────────────────

Initial LangGraph topology for Operonix workflow.
Per migration plan §4.1, baseline lifecycle:

START → INTAKE → OBSERVE → END

This is the foundation pass — no AI, no executor rewrite.
The graph simply demonstrates state flow through nodes.
"""
from __future__ import annotations

import logging
from typing import Any, Dict, Optional
from datetime import datetime

from migration.graph_state import OperonixState
from migration.domain_contracts import TaskRequest, TaskSource
from migration.feature_flags import flags

logger = logging.getLogger("Graph")


# ─── NODE IMPLEMENTATIONS ─────────────────────────────────────────────────────

def intake_node(state: OperonixState) -> Dict[str, Any]:
    """Intake node: Creates task state from user input.
    
    This is a deterministic node that:
    - Validates the task request
    - Creates the initial OperonixState
    - No LLM calls, no AI reasoning
    
    Per migration plan §4.2, node 1:
    "intake — deterministic; creates state.task. Replaces orchestrator.handle_new_task()"
    """
    logger.info(f"INTAKE: Processing task {state.task.task_id}")
    
    # Add history event
    state.add_history_event("intake_started", {
        "task_id": state.task.task_id,
        "user_input": state.task.user_input,
        "source": state.task.source.value
    })
    
    # Update timestamp
    state.update_timestamp()
    
    # In this foundation pass, intake just validates and logs
    # Later phases will add more sophisticated processing
    
    state.add_history_event("intake_completed", {
        "task_id": state.task.task_id
    })
    
    return {"state": state}


def observe_node(state: OperonixState) -> Dict[str, Any]:
    """Observe node: Gathers context about current environment.
    
    This node calls context services to understand the current state:
    - WindowDetector
    - AppClassifier
    - StateExtractor
    - FocusTracker
    - ContextValidator
    
    Per migration plan §4.2, node 2:
    "gather_context / observe — calls WindowDetector, AppClassifier, StateExtractor,
     FocusTracker, ContextValidator; writes state.context"
    
    In Phase 1 foundation, this is a stub that logs the intent.
    Later phases will integrate actual context services.
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


def finalize_node(state: OperonixState) -> Dict[str, Any]:
    """Finalize node: Produces terminal result.
    
    This node creates the FinalResult that will be returned to
    API/Dashboard/Panel/Voice interfaces.
    
    Per migration plan §4.2, node 12:
    "finalize — builds final.success/partial/response/error for API/Dashboard/Panel/Voice"
    """
    logger.info(f"FINALIZE: Finalizing task {state.task.task_id}")
    
    from migration.domain_contracts import FinalResult, TaskStatus
    
    state.add_history_event("finalize_started", {
        "task_id": state.task.task_id
    })
    
    # In Phase 1 foundation, we create a simple success result
    # Later phases will build more sophisticated final results
    final_result = FinalResult(
        success=True,
        response=f"Task {state.task.task_id} completed (Phase 1 foundation)",
        task_id=state.task.task_id
    )
    
    state.final = final_result
    state.add_history_event("finalize_completed", {
        "task_id": state.task.task_id,
        "success": final_result.success
    })
    
    state.update_timestamp()
    
    return {"state": state}


# ─── GRAPH BUILDER ───────────────────────────────────────────────────────────

def build_operonix_graph():
    """Build the initial Operonix LangGraph topology.
    
    Phase 1 topology:
    START → INTAKE → OBSERVE → FINALIZE → END
    
    This is a minimal foundation to prove:
    - LangGraph can be instantiated
    - State flows through nodes
    - Nodes can be called deterministically
    
    Later phases will add:
    - analyze_intent (LangChain integration)
    - retrieve_knowledge (RAG/memory)
    - create_plan (planning)
    - route (routing engine)
    - safety_check (safety integration)
    - execute_step (executor integration)
    - verify_step (verification)
    - recover (recovery logic)
    - reflect (reflection)
    """
    try:
        from langgraph.graph import StateGraph, END
        
        # Create state graph with OperonixState
        workflow = StateGraph(OperonixState)
        
        # Add nodes
        workflow.add_node("intake", intake_node)
        workflow.add_node("observe", observe_node)
        workflow.add_node("finalize", finalize_node)
        
        # Define edges
        workflow.set_entry_point("intake")
        workflow.add_edge("intake", "observe")
        workflow.add_edge("observe", "finalize")
        workflow.add_edge("finalize", END)
        
        # Compile the graph
        compiled_graph = workflow.compile()
        
        logger.info("Operonix LangGraph topology built successfully (Phase 1 foundation)")
        logger.info("Topology: START → INTAKE → OBSERVE → FINALIZE → END")
        
        return compiled_graph
        
    except ImportError:
        logger.warning("LangGraph not installed. Graph will be created when dependencies are installed.")
        logger.info("To install LangGraph, add to requirements.txt: langgraph")
        return None


# ─── GRAPH RUNNER ────────────────────────────────────────────────────────────

class OperonixGraphRunner:
    """Runner for executing Operonix workflows through LangGraph.
    
    This class provides the interface between the Operonix Runtime
    and the LangGraph workflow engine.
    
    Per migration plan §6.1:
    "The runtime creates initial state and invokes the graph; it does NOT
    decide intent, plan, routing, retry, or replanning — those belong to the workflow."
    """
    
    def __init__(self):
        self.graph = build_operonix_graph()
        self.is_enabled = flags.USE_LANGGRAPH
    
    def is_available(self) -> bool:
        """Check if LangGraph is available and enabled."""
        return self.graph is not None and self.is_enabled
    
    async def run_task(self, task_request: TaskRequest) -> OperonixState:
        """Run a task through the LangGraph workflow.
        
        Args:
            task_request: The task request to execute
            
        Returns:
            OperonixState: The final workflow state
            
        Raises:
            RuntimeError: If graph is not available or enabled
        """
        if not self.is_available():
            raise RuntimeError(
                "LangGraph is not available or enabled. "
                "Set USE_LANGGRAPH=true in environment or .env file."
            )
        
        logger.info(f"Running task {task_request.task_id} through LangGraph")
        
        # Create initial state
        initial_state = OperonixState(task=task_request)
        
        # Run the graph
        try:
            # LangGraph's invoke method is synchronous in current version
            # We wrap it in async for future compatibility
            final_state = self.graph.invoke(initial_state)
            
            logger.info(f"Task {task_request.task_id} completed through LangGraph")
            return final_state
            
        except Exception as e:
            logger.error(f"Graph execution failed for task {task_request.task_id}: {e}")
            raise


# ─── GLOBAL GRAPH INSTANCE ─────────────────────────────────────────────────

# Global graph runner instance
graph_runner = OperonixGraphRunner()
