"""
LangGraph Topology — Operonix Migration Phase 1/2
───────────────────────────────────────────────────

LangGraph topology for Operonix workflow.
Per migration plan §4.1, baseline lifecycle:

Phase 1: START → INTAKE → OBSERVE → FINALIZE → END
Phase 2: START → INTAKE → OBSERVE → ANALYZE_INTENT → FINALIZE → END

Phase 1 foundation: no AI, no executor rewrite.
Phase 2: LangChain integration for intent analysis.
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

# Import nodes from nodes package
from graph.nodes.intake import intake_node
from graph.nodes.observe import observe_node
from graph.nodes.finalize import finalize_node
from graph.nodes.analyze_intent import analyze_intent_node
from graph.nodes.create_plan import create_plan_node
from graph.nodes.retrieve_knowledge import retrieve_knowledge_node
from graph.nodes.route import route_node
from graph.nodes.safety_check import safety_check_node
from graph.nodes.execute_step import execute_step_node
from graph.nodes.verify_step import verify_step_node


# ─── GRAPH BUILDER ───────────────────────────────────────────────────────────

def build_operonix_graph():
    """Build the Operonix LangGraph topology.
    
    Phase 4 topology (First Vertical Slice):
    START → INTAKE → OBSERVE → ANALYZE_INTENT → RETRIEVE_KNOWLEDGE → CREATE_PLAN → ROUTE → SAFETY_CHECK → EXECUTE_STEP → VERIFY_STEP → FINALIZE → END
    
    This adds the first vertical slice with retrieve_knowledge, route, safety_check, execute_step, verify_step.
    
    Later phases will add:
    - recover (recovery logic)
    - reflect (reflection)
    - checkpointing (pause/resume)
    """
    try:
        from langgraph.graph import StateGraph, END
        
        # Create state graph with OperonixState
        workflow = StateGraph(OperonixState)
        
        # Add nodes
        workflow.add_node("intake", intake_node)
        workflow.add_node("observe", observe_node)
        workflow.add_node("analyze_intent", analyze_intent_node)
        workflow.add_node("retrieve_knowledge", retrieve_knowledge_node)
        workflow.add_node("create_plan", create_plan_node)
        workflow.add_node("route", route_node)
        workflow.add_node("safety_check", safety_check_node)
        workflow.add_node("execute_step", execute_step_node)
        workflow.add_node("verify_step", verify_step_node)
        workflow.add_node("finalize", finalize_node)
        
        # Define edges
        workflow.set_entry_point("intake")
        workflow.add_edge("intake", "observe")
        workflow.add_edge("observe", "analyze_intent")
        workflow.add_edge("analyze_intent", "retrieve_knowledge")
        workflow.add_edge("retrieve_knowledge", "create_plan")
        workflow.add_edge("create_plan", "route")
        workflow.add_edge("route", "safety_check")
        workflow.add_edge("safety_check", "execute_step")
        workflow.add_edge("execute_step", "verify_step")
        workflow.add_edge("verify_step", "finalize")
        workflow.add_edge("finalize", END)
        
        # Compile the graph
        compiled_graph = workflow.compile()
        
        logger.info("Operonix LangGraph topology built successfully (Phase 4)")
        logger.info("Topology: START → INTAKE → OBSERVE → ANALYZE_INTENT → RETRIEVE_KNOWLEDGE → CREATE_PLAN → ROUTE → SAFETY_CHECK → EXECUTE_STEP → VERIFY_STEP → FINALIZE → END")
        
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
