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
    resume_manager = get_resume_manager()
    
    try:
        confirmation = resume_manager.get_confirmation(task_id)
        
        if not confirmation:
            raise HTTPException(status_code=404, detail=f"No pending confirmation found for task {task_id}")
        
        return confirmation
        
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
    resume_manager = get_resume_manager()
    
    try:
        result = resume_manager.resume_workflow(
            request.task_id,
            request.response,
            request.response_data
        )
        
        if result.get("status") == "error":
            raise HTTPException(status_code=400, detail=result.get("message"))
        
        return result
        
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
    resume_manager = get_resume_manager()
    
    try:
        result = resume_manager.cancel_confirmation(task_id)
        
        if result.get("status") == "error":
            raise HTTPException(status_code=404, detail=result.get("message"))
        
        return result
        
    except HTTPException:
        raise
    except Exception as e:
        logger.error(f"Failed to cancel confirmation: {e}")
        raise HTTPException(status_code=500, detail=str(e))
