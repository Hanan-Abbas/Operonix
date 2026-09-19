"""
api/routes/confirmation.py

Confirmation and human intervention endpoints.

Provides endpoints for:
- Listing pending confirmations
- Submitting human responses (CONFIRM, DENY, etc.)
- Resuming paused workflows
"""

import logging
from typing import Any, Dict, List, Optional

from fastapi import APIRouter, HTTPException, Query
from pydantic import BaseModel

from migration.domain_contracts import HumanInterventionType
from graph.resume_manager import get_resume_manager

logger = logging.getLogger("ConfirmationRoute")

router = APIRouter(prefix="/api/confirmations", tags=["confirmations"])


# ─────────────────────────────────────────────────────────────────────────────
# Request/Response Models
# ─────────────────────────────────────────────────────────────────────────────

class HumanResponseRequest(BaseModel):
    """Request model for human response to confirmation."""
    task_id: str
    response: str  # CONFIRM, DENY, CLARIFY, CHOOSE, PROVIDE_INFORMATION, TAKE_OVER, ABORT
    response_data: Optional[Dict[str, Any]] = None


class ConfirmationInfo(BaseModel):
    """Information about a pending confirmation."""
    task_id: str
    intervention_type: str
    reason: str
    context: Dict[str, Any]
    checkpoint_identifier: str
    requested_at: str


# ─────────────────────────────────────────────────────────────────────────────
# Endpoints
# ─────────────────────────────────────────────────────────────────────────────

@router.get("/pending")
async def list_pending_confirmations() -> Dict[str, Any]:
    """
    📋 List all pending confirmations (paused workflows awaiting human response).
    
    Returns:
        Dict with list of pending confirmations
    """
    resume_manager = get_resume_manager()
    
    try:
        pending_confirmations = resume_manager.get_pending_confirmations()
        return {
            "confirmations": pending_confirmations,
            "count": len(pending_confirmations)
        }
        
    except Exception as e:
        logger.error(f"Failed to list pending confirmations: {e}")
        raise HTTPException(status_code=500, detail=str(e))


@router.get("/{task_id}")
async def get_confirmation_by_task(task_id: str) -> Dict[str, Any]:
    """
    🔍 Get confirmation details for a specific task.
    
    Args:
        task_id: Task identifier
        
    Returns:
        Dict with confirmation details
    """
    checkpointing_service = get_checkpointing_service()
    
    try:
        # Get latest checkpoint for task
        checkpoint = checkpointing_service.get_latest_checkpoint(task_id)
        
        if not checkpoint:
            raise HTTPException(status_code=404, detail=f"No checkpoint found for task {task_id}")
        
        # Check if state is paused
        state_dict = checkpoint.workflow_state
        if not state_dict.get('paused', False):
            raise HTTPException(status_code=400, detail=f"Task {task_id} is not paused")
        
        # Extract confirmation info
        confirmation_data = state_dict.get('confirmation')
        if not confirmation_data:
            raise HTTPException(status_code=404, detail=f"No confirmation data for task {task_id}")
        
        return {
            "task_id": task_id,
            "intervention_type": confirmation_data.get('intervention_type'),
            "reason": confirmation_data.get('reason'),
            "context": confirmation_data.get('context', {}),
            "checkpoint_identifier": checkpoint.checkpoint_identifier,
            "requested_at": confirmation_data.get('requested_at'),
            "options": confirmation_data.get('options', [])
        }
        
    except HTTPException:
        raise
    except Exception as e:
        logger.error(f"Failed to get confirmation for task {task_id}: {e}")
        raise HTTPException(status_code=500, detail=str(e))


@router.post("/respond")
async def respond_to_confirmation(request: HumanResponseRequest) -> Dict[str, Any]:
    """
    ✍️ Submit human response to confirmation and resume workflow.
    
    This endpoint:
    1. Loads the checkpoint for the task
    2. Restores the workflow state
    3. Applies the human response
    4. Resumes graph execution from confirmation node
    
    Args:
        request: Human response request
        
    Returns:
        Dict with resume status
    """
    checkpointing_service = get_checkpointing_service()
    
    try:
        # Validate response type
        try:
            response_type = HumanInterventionType(request.response.upper())
        except ValueError:
            valid_responses = [t.value for t in HumanInterventionType]
            raise HTTPException(
                status_code=400,
                detail=f"Invalid response '{request.response}'. Valid responses: {valid_responses}"
            )
        
        # Get latest checkpoint for task
        checkpoint = checkpointing_service.get_latest_checkpoint(request.task_id)
        
        if not checkpoint:
            raise HTTPException(status_code=404, detail=f"No checkpoint found for task {request.task_id}")
        
        # Check if state is paused
        state_dict = checkpoint.workflow_state
        if not state_dict.get('paused', False):
            raise HTTPException(status_code=400, detail=f"Task {request.task_id} is not paused")
        
        # Restore state from checkpoint
        logger.info(f"Restoring state from checkpoint {checkpoint.checkpoint_identifier}")
        state = checkpointing_service.restore_state(checkpoint)
        
        if not state:
            raise HTTPException(status_code=500, detail="Failed to restore state from checkpoint")
        
        # Apply human response
        logger.info(f"Applying human response {response_type.value} to task {request.task_id}")
        state_update = resume_from_confirmation(state, response_type)
        
        # Add response data if provided
        if request.response_data:
            if state.confirmation:
                state.confirmation.response_data = request.response_data
        
        # Resume graph execution
        logger.info(f"Resuming graph execution for task {request.task_id}")
        
        try:
            from graph.graph import graph_runner
            if not graph_runner or not graph_runner.is_available():
                raise HTTPException(status_code=503, detail="Graph runner not available")
            
            # Resume graph from confirmation node
            # We need to continue execution from where it left off
            # LangGraph doesn't have built-in resume from checkpoint, so we re-invoke
            # with the restored state
            final_state = await graph_runner.run_task(state.task)
            
            logger.info(f"Task {request.task_id} resumed and completed")
            
            return {
                "status": "success",
                "message": f"Task {request.task_id} resumed with response {response_type.value}",
                "task_id": request.task_id,
                "response": response_type.value,
                "final_state": {
                    "success": final_state.final.success if final_state.final else None,
                    "response": final_state.final.response if final_state.final else None
                }
            }
            
        except Exception as e:
            logger.error(f"Failed to resume graph execution: {e}")
            raise HTTPException(status_code=500, detail=f"Failed to resume graph: {str(e)}")
        
    except HTTPException:
        raise
    except Exception as e:
        logger.error(f"Failed to respond to confirmation: {e}")
        raise HTTPException(status_code=500, detail=str(e))


@router.delete("/{task_id}")
async def cancel_confirmation(task_id: str) -> Dict[str, Any]:
    """
    ❌ Cancel a pending confirmation and abort the workflow.
    
    Args:
        task_id: Task identifier
        
    Returns:
        Dict with cancellation status
    """
    checkpointing_service = get_checkpointing_service()
    
    try:
        # Get latest checkpoint for task
        checkpoint = checkpointing_service.get_latest_checkpoint(task_id)
        
        if not checkpoint:
            raise HTTPException(status_code=404, detail=f"No checkpoint found for task {task_id}")
        
        # Delete checkpoint
        checkpointing_service.delete_checkpoint(checkpoint.checkpoint_identifier)
        
        logger.info(f"Cancelled confirmation for task {task_id}, checkpoint deleted")
        
        return {
            "status": "success",
            "message": f"Confirmation for task {task_id} cancelled",
            "task_id": task_id
        }
        
    except HTTPException:
        raise
    except Exception as e:
        logger.error(f"Failed to cancel confirmation: {e}")
        raise HTTPException(status_code=500, detail=str(e))
