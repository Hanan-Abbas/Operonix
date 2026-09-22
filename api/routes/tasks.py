"""
api/routes/tasks.py

Task submission endpoints for LangGraph workflow.

Provides endpoints for:
- Submitting tasks to the LangGraph workflow
- Checking task status
- Getting task results
- Switching between graph and legacy execution
"""

import logging
from typing import Any, Dict, Optional

from fastapi import APIRouter, HTTPException, Query
from pydantic import BaseModel

from migration.domain_contracts import TaskSource
from graph.runtime_adapter import runtime_adapter

logger = logging.getLogger("TasksRoute")

router = APIRouter(prefix="/api/tasks", tags=["tasks"])


# ─────────────────────────────────────────────────────────────────────────────
# Request/Response Models
# ─────────────────────────────────────────────────────────────────────────────

class TaskSubmissionRequest(BaseModel):
    """Request model for task submission."""
    user_input: str
    source: str = "api"  # voice, panel, api, cli
    metadata: Optional[Dict[str, Any]] = None
    use_graph: Optional[bool] = None  # Force graph usage (None = use feature flag)


class TaskSubmissionResponse(BaseModel):
    """Response model for task submission."""
    task_id: str
    status: str
    success: bool
    response: str
    paused: bool
    error: Optional[str] = None
    execution_method: str  # "graph" or "legacy"


class TaskStatusResponse(BaseModel):
    """Response model for task status."""
    task_id: str
    status: str
    message: str


# ─────────────────────────────────────────────────────────────────────────────
# Endpoints
# ─────────────────────────────────────────────────────────────────────────────

@router.post("/submit")
async def submit_task(request: TaskSubmissionRequest) -> Dict[str, Any]:
    """
    📝 Submit a task to the Operonix workflow.
    
    This endpoint:
    1. Creates a TaskRequest from the user input
    2. Routes to either LangGraph or legacy workflow based on feature flags
    3. Returns the task result
    
    Args:
        request: Task submission request
        
    Returns:
        Dict with task result
    """
    try:
        # Convert source to TaskSource
        source_map = {
            "voice": TaskSource.VOICE,
            "panel": TaskSource.PANEL,
            "api": TaskSource.API,
            "cli": TaskSource.CLI,
        }
        task_source = source_map.get(request.source.lower(), TaskSource.API)
        
        # Create task request
        task_request = runtime_adapter.create_task_request(
            user_input=request.user_input,
            source=task_source,
            metadata=request.metadata or {}
        )
        
        # Determine execution method
        should_use_graph = request.use_graph if request.use_graph is not None else runtime_adapter.is_graph_enabled()
        execution_method = "graph" if should_use_graph else "legacy"
        
        logger.info(f"API task submission: task_id={task_request.task_id}, method={execution_method}")
        
        # Execute task
        result = await runtime_adapter.execute_task(task_request, use_graph=should_use_graph)
        
        return {
            "task_id": task_request.task_id,
            "status": "completed" if result.success else "failed",
            "success": result.success,
            "response": result.response,
            "paused": result.paused,
            "error": result.error,
            "execution_method": execution_method,
            "checkpoint_identifier": result.checkpoint_identifier if result.paused else None
        }
        
    except Exception as e:
        logger.error(f"Failed to submit task: {e}")
        raise HTTPException(status_code=500, detail=str(e))


@router.get("/status")
async def get_system_status() -> Dict[str, Any]:
    """
    📊 Get the current status of the task execution system.
    
    Returns:
        Dict with system status including graph availability and feature flags
    """
    try:
        graph_status = runtime_adapter.get_graph_status()
        
        return {
            "graph_enabled": graph_status["graph_enabled"],
            "graph_available": graph_status["graph_available"],
            "migration_phase": graph_status["migration_phase"],
            "feature_flags": graph_status["all_flags"],
            "runtime_adapter_available": runtime_adapter is not None
        }
        
    except Exception as e:
        logger.error(f"Failed to get system status: {e}")
        raise HTTPException(status_code=500, detail=str(e))


@router.post("/switch")
async def switch_execution_method(use_graph: bool = Query(..., description="Enable or disable graph execution")) -> Dict[str, Any]:
    """
    🔄 Switch between graph and legacy execution methods.
    
    This is a runtime switch for testing purposes. In production, the
    USE_LANGGRAPH environment variable should be used instead.
    
    Args:
        use_graph: Whether to enable graph execution
        
    Returns:
        Dict with switch status
    """
    try:
        from migration.feature_flags import flags
        
        # Note: This changes the runtime flag but won't persist
        # For persistent changes, set USE_LANGGRAPH in .env file
        old_value = flags.USE_LANGGRAPH
        flags.USE_LANGGRAPH = use_graph
        
        logger.info(f"Execution method switched: {old_value} → {use_graph}")
        
        return {
            "status": "success",
            "previous_method": "graph" if old_value else "legacy",
            "current_method": "graph" if use_graph else "legacy",
            "note": "This change is runtime-only. Set USE_LANGGRAPH in .env for persistence."
        }
        
    except Exception as e:
        logger.error(f"Failed to switch execution method: {e}")
        raise HTTPException(status_code=500, detail=str(e))


@router.get("/graph/status")
async def get_graph_status() -> Dict[str, Any]:
    """
    🔍 Get detailed LangGraph status and configuration.
    
    Returns:
        Dict with detailed graph status
    """
    try:
        graph_status = runtime_adapter.get_graph_status()
        
        # Add additional graph-specific information
        from graph.graph import graph_runner
        
        return {
            **graph_status,
            "graph_runner_available": graph_runner is not None,
            "graph_runner_enabled": graph_runner.is_available() if graph_runner else False,
            "runtime_adapter_initialized": runtime_adapter.graph_runner is not None
        }
        
    except Exception as e:
        logger.error(f"Failed to get graph status: {e}")
        raise HTTPException(status_code=500, detail=str(e))
