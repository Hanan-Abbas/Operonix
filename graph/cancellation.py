"""
Cancellation Service — Operonix Graph
────────────────────────────────────

Cancellation service for workflow cancellation.
Per migration plan Phase 8: Cancellation, Timeout & Resource Control
"""
from __future__ import annotations

import logging
import threading
from typing import Dict, Any, Optional, Set
from datetime import datetime

from migration.domain_contracts import CancellationRequest, CancellationReason, AbortDecision, AbortSemantics
from migration.graph_state import OperonixState
from graph.timeout_manager import get_timeout_manager

logger = logging.getLogger("Graph.Cancellation")


class ResourceContentionDetector:
    """Detector for resource contention between workflows.
    
    This class tracks which resources are being used by which tasks
    and detects when multiple workflows try to access the same physical resource.
    """
    
    def __init__(self):
        """Initialize resource contention detector."""
        self.resource_owners: Dict[str, Set[str]] = {}  # resource -> set of task_ids
        self.task_resources: Dict[str, Set[str]] = {}  # task_id -> set of resources
        self._lock = threading.Lock()
        
        logger.info("ResourceContentionDetector initialized")
    
    def acquire_resource(self, task_id: str, resource: str) -> bool:
        """Attempt to acquire a resource for a task.
        
        Args:
            task_id: Task identifier
            resource: Resource identifier (e.g., file path, device name, etc.)
            
        Returns:
            True if resource acquired, False if contention detected
        """
        with self._lock:
            # Check if resource is already owned by another task
            if resource in self.resource_owners:
                existing_owners = self.resource_owners[resource]
                if task_id not in existing_owners and len(existing_owners) > 0:
                    # Resource contention detected
                    logger.warning(f"Resource contention detected: {resource} owned by {existing_owners}, requested by {task_id}")
                    return False
            
            # Acquire resource
            if resource not in self.resource_owners:
                self.resource_owners[resource] = set()
            self.resource_owners[resource].add(task_id)
            
            if task_id not in self.task_resources:
                self.task_resources[task_id] = set()
            self.task_resources[task_id].add(resource)
            
            logger.debug(f"Task {task_id} acquired resource: {resource}")
            return True
    
    def release_resource(self, task_id: str, resource: str) -> None:
        """Release a resource from a task.
        
        Args:
            task_id: Task identifier
            resource: Resource identifier
        """
        with self._lock:
            if resource in self.resource_owners:
                self.resource_owners[resource].discard(task_id)
                if not self.resource_owners[resource]:
                    del self.resource_owners[resource]
            
            if task_id in self.task_resources:
                self.task_resources[task_id].discard(resource)
                if not self.task_resources[task_id]:
                    del self.task_resources[task_id]
            
            logger.debug(f"Task {task_id} released resource: {resource}")
    
    def release_all_resources(self, task_id: str) -> int:
        """Release all resources held by a task.
        
        Args:
            task_id: Task identifier
            
        Returns:
            Number of resources released
        """
        with self._lock:
            if task_id not in self.task_resources:
                return 0
            
            resources = list(self.task_resources[task_id])
            count = 0
            for resource in resources:
                self.release_resource(task_id, resource)
                count += 1
            
            logger.info(f"Task {task_id} released {count} resources")
            return count
    
    def check_contention(self, task_id: str, resource: str) -> bool:
        """Check if acquiring a resource would cause contention.
        
        Args:
            task_id: Task identifier
            resource: Resource identifier
            
        Returns:
            True if contention would occur, False otherwise
        """
        with self._lock:
            if resource in self.resource_owners:
                existing_owners = self.resource_owners[resource]
                return task_id not in existing_owners and len(existing_owners) > 0
            return False
    
    def get_resource_owners(self, resource: str) -> Set[str]:
        """Get owners of a resource.
        
        Args:
            resource: Resource identifier
            
        Returns:
            Set of task IDs that own the resource
        """
        with self._lock:
            return self.resource_owners.get(resource, set()).copy()
    
    def get_task_resources(self, task_id: str) -> Set[str]:
        """Get resources held by a task.
        
        Args:
            task_id: Task identifier
            
        Returns:
            Set of resource identifiers held by the task
        """
        with self._lock:
            return self.task_resources.get(task_id, set()).copy()
    
    def detect_conflicts(self) -> Dict[str, Set[str]]:
        """Detect all current resource conflicts.
        
        Returns:
            Dict mapping resource to set of conflicting task IDs
        """
        conflicts = {}
        with self._lock:
            for resource, owners in self.resource_owners.items():
                if len(owners) > 1:
                    conflicts[resource] = owners.copy()
        return conflicts


class CancellationService:
    """Service for workflow cancellation and abort."""
    
    def __init__(self):
        """Initialize cancellation service."""
        self.active_cancellations: Dict[str, CancellationRequest] = {}
        self.abort_decisions: Dict[str, AbortDecision] = {}
        self.timeout_manager = get_timeout_manager()
        self.resource_detector = ResourceContentionDetector()
        
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
    
    def try_acquire_resource(self, task_id: str, resource: str) -> bool:
        """Try to acquire a resource for a task.
        
        This method uses the resource contention detector to check if the resource
        can be safely acquired without causing contention.
        
        Args:
            task_id: Task identifier
            resource: Resource identifier
            
        Returns:
            True if resource acquired, False if contention detected
        """
        return self.resource_detector.acquire_resource(task_id, resource)
    
    def release_task_resource(self, task_id: str, resource: str) -> None:
        """Release a resource from a task.
        
        Args:
            task_id: Task identifier
            resource: Resource identifier
        """
        self.resource_detector.release_resource(task_id, resource)
    
    def release_all_task_resources(self, task_id: str) -> int:
        """Release all resources held by a task.
        
        This is typically called when a task completes or is cancelled.
        
        Args:
            task_id: Task identifier
            
        Returns:
            Number of resources released
        """
        return self.resource_detector.release_all_resources(task_id)
    
    def check_resource_contention(self, task_id: str, resource: str) -> bool:
        """Check if acquiring a resource would cause contention.
        
        Args:
            task_id: Task identifier
            resource: Resource identifier
            
        Returns:
            True if contention would occur, False otherwise
        """
        return self.resource_detector.check_contention(task_id, resource)
    
    def detect_all_resource_conflicts(self) -> Dict[str, Set[str]]:
        """Detect all current resource conflicts.
        
        Returns:
            Dict mapping resource to set of conflicting task IDs
        """
        return self.resource_detector.detect_conflicts()
    
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
