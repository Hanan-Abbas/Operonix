"""
Cancellation Service — Operonix Graph
────────────────────────────────────

Cancellation service for workflow cancellation.
Per migration plan Phase 8: Cancellation, Timeout & Resource Control
"""
from __future__ import annotations

import logging
from typing import Dict, Any, Optional
from datetime import datetime

from migration.domain_contracts import CancellationRequest, CancellationReason, AbortDecision, AbortSemantics
from migration.graph_state import OperonixState
from graph.timeout_manager import get_timeout_manager

logger = logging.getLogger("Graph.Cancellation")


class CancellationService:
    """Service for workflow cancellation and abort."""
    
    def __init__(self):
        """Initialize cancellation service."""
        self.active_cancellations: Dict[str, CancellationRequest] = {}
        self.abort_decisions: Dict[str, AbortDecision] = {}
        self.timeout_manager = get_timeout_manager()
        
        logger.info("CancellationService initialized")
    
    def request_cancellation(self, task_id: str, reason: CancellationReason, requested_by: str = "system", context: Optional[Dict[str, Any]] = None) -> CancellationRequest:
        """Request cancellation of a workflow.
        
        Args:
            task_id: Task identifier
            reason: Reason for cancellation
            requested_by: Who requested cancellation (user, system, watchdog)
            context: Additional context
            
        Returns:
            CancellationRequest
        """
        cancellation = CancellationRequest(
            task_id=task_id,
            reason=reason,
            requested_by=requested_by,
            context=context or {}
        )
        
        self.active_cancellations[cancellation.cancellation_id] = cancellation
        
        # Cancel all timeouts for this task
        self.timeout_manager.cancel_all_timeouts_for_task(task_id)
        
        logger.info(f"Cancellation requested: {cancellation.cancellation_id} for task {task_id} (reason: {reason.value})")
        
        return cancellation
    
    def cancel_workflow(self, state: OperonixState, cancellation: CancellationRequest) -> Dict[str, Any]:
        """Cancel workflow execution.
        
        Args:
            state: Current OperonixState
            cancellation: Cancellation request
            
        Returns:
            Dict with updated state
        """
        logger.info(f"Cancelling workflow for task {state.task.task_id} (reason: {cancellation.reason.value})")
        
        state.add_history_event("cancellation_requested", {
            "task_id": state.task.task_id,
            "cancellation_id": cancellation.cancellation_id,
            "reason": cancellation.reason.value,
            "requested_by": cancellation.requested_by
        })
        
        # Mark state as cancelled
        state.cancelled = True
        state.cancellation = cancellation
        
        # Determine abort semantics based on cancellation reason
        abort_semantics = self._determine_abort_semantics(cancellation.reason)
        
        # Create abort decision
        abort_decision = AbortDecision(
            task_id=state.task.task_id,
            semantics=abort_semantics,
            reason=f"Cancellation: {cancellation.reason.value}",
            cleanup_required=True,
            rollback_required=cancellation.reason == CancellationReason.TIMEOUT
        )
        
        self.abort_decisions[abort_decision.abort_id] = abort_decision
        state.abort_decision = abort_decision
        
        state.add_history_event("workflow_cancelled", {
            "task_id": state.task.task_id,
            "abort_semantics": abort_semantics.value,
            "cleanup_required": abort_decision.cleanup_required
        })
        
        state.update_timestamp()
        
        return {"state": state}
    
    def _determine_abort_semantics(self, reason: CancellationReason) -> AbortSemantics:
        """Determine abort semantics based on cancellation reason.
        
        Args:
            reason: Cancellation reason
            
        Returns:
            AbortSemantics
        """
        if reason == CancellationReason.USER_REQUESTED:
            return AbortSemantics.GRACEFUL
        elif reason == CancellationReason.TIMEOUT:
            return AbortSemantics.SAFE
        elif reason == CancellationReason.SAFE_ABORT:
            return AbortSemantics.SAFE
        elif reason == CancellationReason.RESOURCE_CONTENTION:
            return AbortSemantics.GRACEFUL
        elif reason == CancellationReason.SYSTEM_ERROR:
            return AbortSemantics.IMMEDIATE
        else:
            return AbortSemantics.GRACEFUL
    
    def handle_user_cancellation(self, state: OperonixState) -> Dict[str, Any]:
        """Handle user-requested cancellation.
        
        Args:
            state: Current OperonixState
            
        Returns:
            Dict with updated state
        """
        cancellation = self.request_cancellation(
            task_id=state.task.task_id,
            reason=CancellationReason.USER_REQUESTED,
            requested_by="user"
        )
        
        return self.cancel_workflow(state, cancellation)
    
    def handle_timeout_cancellation(self, state: OperonixState, timeout_type: str) -> Dict[str, Any]:
        """Handle timeout cancellation.
        
        Args:
            state: Current OperonixState
            timeout_type: Type of timeout (operation, step, task, watchdog)
            
        Returns:
            Dict with updated state
        """
        cancellation = self.request_cancellation(
            task_id=state.task.task_id,
            reason=CancellationReason.TIMEOUT,
            requested_by="watchdog",
            context={"timeout_type": timeout_type}
        )
        
        return self.cancel_workflow(state, cancellation)
    
    def handle_resource_contention(self, state: OperonixState, resource_type: str) -> Dict[str, Any]:
        """Handle resource contention cancellation.
        
        Args:
            state: Current OperonixState
            resource_type: Type of resource with contention
            
        Returns:
            Dict with updated state
        """
        cancellation = self.request_cancellation(
            task_id=state.task.task_id,
            reason=CancellationReason.RESOURCE_CONTENTION,
            requested_by="system",
            context={"resource_type": resource_type}
        )
        
        return self.cancel_workflow(state, cancellation)
    
    def get_cancellation(self, task_id: str) -> Optional[CancellationRequest]:
        """Get cancellation request for a task.
        
        Args:
            task_id: Task identifier
            
        Returns:
            CancellationRequest if found, None otherwise
        """
        for cancellation in self.active_cancellations.values():
            if cancellation.task_id == task_id:
                return cancellation
        return None


# Global cancellation service instance
_cancellation_service: Optional[CancellationService] = None


def get_cancellation_service() -> CancellationService:
    """Get the global cancellation service instance.
    
    Returns:
        CancellationService instance
    """
    global _cancellation_service
    
    if _cancellation_service is None:
        _cancellation_service = CancellationService()
    
    return _cancellation_service
