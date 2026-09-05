"""
Runtime ↔ Graph Adapter — Operonix Migration Phase 1
────────────────────────────────────────────────────

Adapter between Operonix Runtime and LangGraph workflow.
Per migration plan §6.1:
"The runtime creates initial state and invokes the graph; it does NOT
decide intent, plan, routing, retry, or replanning — those belong to the workflow."

This adapter provides:
- Task request to OperonixState conversion
- Graph invocation
- Final result extraction
- Fallback to legacy workflow when graph is disabled
"""
from __future__ import annotations

import logging
from typing import Optional, Dict, Any
from datetime import datetime

from migration.graph_state import OperonixState
from migration.domain_contracts import TaskRequest, TaskSource, FinalResult
from migration.feature_flags import flags

logger = logging.getLogger("RuntimeAdapter")


class RuntimeGraphAdapter:
    """Adapter between Operonix Runtime and LangGraph workflow.
    
    This adapter is the boundary between the Operonix Runtime and the
    LangGraph workflow engine. It is responsible for:
    - Converting task requests to graph state
    - Invoking the graph
    - Extracting final results
    - Providing fallback to legacy workflow when graph is disabled
    
    Per migration plan §6.1, the runtime creates initial state and invokes
    the graph, but does NOT decide workflow strategy.
    """
    
    def __init__(self):
        self.graph_runner = None
        self._initialize_graph()
    
    def _initialize_graph(self) -> None:
        """Initialize the graph runner."""
        try:
            from graph.graph import graph_runner
            self.graph_runner = graph_runner
            logger.info("RuntimeGraphAdapter: Graph runner initialized")
        except ImportError as e:
            logger.warning(f"RuntimeGraphAdapter: Could not import graph runner: {e}")
            self.graph_runner = None
    
    def is_graph_enabled(self) -> bool:
        """Check if LangGraph workflow is enabled and available."""
        return flags.USE_LANGGRAPH and self.graph_runner is not None and self.graph_runner.is_available()
    
    def create_task_request(
        self,
        user_input: str,
        source: TaskSource,
        metadata: Optional[Dict[str, Any]] = None
    ) -> TaskRequest:
        """Create a TaskRequest from user input.
        
        Args:
            user_input: The user's natural language request
            source: The source of the request (VOICE, PANEL, API, CLI)
            metadata: Optional additional metadata
            
        Returns:
            TaskRequest object
        """
        import uuid
        
        return TaskRequest(
            task_id=str(uuid.uuid4()),
            user_input=user_input,
            source=source,
            created_at=datetime.utcnow(),
            metadata=metadata or {}
        )
    
    async def execute_task(
        self,
        task_request: TaskRequest,
        use_graph: Optional[bool] = None
    ) -> FinalResult:
        """Execute a task through either graph or legacy workflow.
        
        Args:
            task_request: The task request to execute
            use_graph: Force graph usage (None = use feature flag)
            
        Returns:
            FinalResult with task outcome
            
        Raises:
            RuntimeError: If neither graph nor legacy workflow is available
        """
        should_use_graph = use_graph if use_graph is not None else self.is_graph_enabled()
        
        if should_use_graph:
            logger.info(f"Executing task {task_request.task_id} through LangGraph workflow")
            return await self._execute_with_graph(task_request)
        else:
            logger.info(f"Executing task {task_request.task_id} through legacy workflow")
            return await self._execute_with_legacy(task_request)
    
    async def _execute_with_graph(self, task_request: TaskRequest) -> FinalResult:
        """Execute task through LangGraph workflow.
        
        Args:
            task_request: The task request to execute
            
        Returns:
            FinalResult with task outcome
            
        Raises:
            RuntimeError: If graph is not available
        """
        if not self.graph_runner or not self.graph_runner.is_available():
            raise RuntimeError("LangGraph workflow is not available")
        
        try:
            # Run task through graph
            final_state = await self.graph_runner.run_task(task_request)
            
            # Extract final result
            if final_state.final:
                logger.info(f"Task {task_request.task_id} completed successfully through graph")
                return final_state.final
            else:
                # Fallback: create final result from state
                logger.warning(f"Task {task_request.task_id} completed but no final result in state")
                return FinalResult(
                    success=True,
                    response=f"Task {task_request.task_id} completed (no explicit final result)",
                    task_id=task_request.task_id
                )
                
        except Exception as e:
            logger.error(f"Graph execution failed for task {task_request.task_id}: {e}")
            # Create error result
            return FinalResult(
                success=False,
                response=f"Task execution failed: {str(e)}",
                error=str(e),
                task_id=task_request.task_id
            )
    
    async def _execute_with_legacy(self, task_request: TaskRequest) -> FinalResult:
        """Execute task through legacy workflow.
        
        In Phase 1, this is a stub that returns a placeholder result.
        Later phases will integrate with the actual legacy orchestrator.
        
        Args:
            task_request: The task request to execute
            
        Returns:
            FinalResult with task outcome
        """
        logger.info(f"Legacy workflow execution for task {task_request.task_id}")
        
        # In Phase 1, we don't actually integrate with legacy orchestrator
        # We return a placeholder result
        # Later phases will integrate with:
        # - from core.orchestrator import Orchestrator
        # - orchestrator.handle_new_task(...)
        
        logger.warning("Legacy workflow integration deferred to later phases")
        
        return FinalResult(
            success=True,
            response=f"Task {task_request.task_id} would execute through legacy workflow (integration deferred)",
            task_id=task_request.task_id
        )
    
    def get_graph_status(self) -> Dict[str, Any]:
        """Get the current status of the graph workflow.
        
        Returns:
            Dict with graph status information
        """
        return {
            "graph_enabled": flags.USE_LANGGRAPH,
            "graph_available": self.graph_runner is not None and self.graph_runner.is_available() if self.graph_runner else False,
            "migration_phase": flags.get_migration_phase(),
            "all_flags": flags.get_all_flags()
        }


# ─── GLOBAL ADAPTER INSTANCE ─────────────────────────────────────────────────

# Global runtime adapter instance
runtime_adapter = RuntimeGraphAdapter()
