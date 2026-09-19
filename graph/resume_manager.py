"""
graph/resume_manager.py

Resume Manager — External Resume Mechanism for LangGraph
────────────────────────────────────────────────────────

Per migration plan Phase 7: Checkpointing, Pause/Resume & Human Intervention

This module provides the external resume mechanism for paused workflows.
It integrates with:
- EventBus for panel integration
- API endpoints for dashboard integration
- CheckpointingService for state restoration
- LangGraph for resuming execution

Flow:
1. Confirmation node pauses graph and creates checkpoint
2. Panel/Dashboard shows confirmation UI to user
3. User responds via panel (EventBus) or dashboard (API)
4. ResumeManager receives response, restores checkpoint, applies response
5. Graph execution resumes from confirmation node
"""
from __future__ import annotations

import logging
from typing import Any, Dict, Optional
from datetime import UTC

from migration.domain_contracts import HumanInterventionType
from migration.graph_state import OperonixState
from graph.checkpointing import get_checkpointing_service
from graph.nodes.confirmation import resume_from_confirmation

logger = logging.getLogger("ResumeManager")


class ResumeManager:
    """Manages external resume of paused workflows.
    
    This class provides the interface for external systems (panel, dashboard, API)
    to resume paused workflows after human intervention.
    """
    
    def __init__(self, event_bus: Optional[Any] = None):
        """Initialize resume manager.
        
        Args:
            event_bus: Optional EventBus instance for panel integration
        """
        self._event_bus = event_bus
        self._checkpointing_service = get_checkpointing_service()
        
        # Subscribe to EventBus if provided
        if event_bus:
            self._subscribe_to_event_bus()
    
    def _subscribe_to_event_bus(self) -> None:
        """Subscribe to EventBus for panel integration."""
        try:
            self._event_bus.subscribe(
                "user_response_received",
                self._on_user_response_received
            )
            logger.info("ResumeManager: Subscribed to user_response_received event")
        except Exception as e:
            logger.warning(f"ResumeManager: Failed to subscribe to EventBus: {e}")
    
    def _on_user_response_received(self, event: Any) -> None:
        """Handle user response from panel via EventBus.
        
        Args:
            event: EventBus event with user response
        """
        try:
            payload = event.data if hasattr(event, "data") else event
            if not isinstance(payload, dict):
                logger.warning("ResumeManager: Invalid payload in user_response_received")
                return
            
            task_id = payload.get("task_id")
            response = payload.get("response")
            response_data = payload.get("response_data")
            
            if not task_id or not response:
                logger.warning("ResumeManager: Missing task_id or response in payload")
                return
            
            logger.info(f"ResumeManager: Received user response for task {task_id}: {response}")
            
            # Resume the workflow
            self.resume_workflow(task_id, response, response_data)
            
        except Exception as e:
            logger.error(f"ResumeManager: Failed to handle user_response_received: {e}")
    
    def get_pending_confirmations(self) -> list[Dict[str, Any]]:
        """Get all pending confirmations (paused workflows).
        
        Returns:
            List of pending confirmation info
        """
        try:
            import os
            checkpoint_dir = self._checkpointing_service.checkpoint_dir
            if not os.path.exists(checkpoint_dir):
                return []
            
            pending_confirmations = []
            
            for filename in os.listdir(checkpoint_dir):
                if not filename.endswith('.json'):
                    continue
                
                try:
                    checkpoint_id = filename[:-5]
                    checkpoint = self._checkpointing_service.load_checkpoint(checkpoint_id)
                    
                    if checkpoint and checkpoint.workflow_state:
                        state_dict = checkpoint.workflow_state
                        if state_dict.get('paused', False):
                            confirmation_data = state_dict.get('confirmation')
                            if confirmation_data:
                                pending_confirmations.append({
                                    "task_id": checkpoint.task_id,
                                    "intervention_type": confirmation_data.get('intervention_type'),
                                    "reason": confirmation_data.get('reason'),
                                    "context": confirmation_data.get('context', {}),
                                    "checkpoint_identifier": checkpoint.checkpoint_identifier,
                                    "requested_at": confirmation_data.get('requested_at')
                                })
                except Exception as e:
                    logger.warning(f"ResumeManager: Failed to load checkpoint {filename}: {e}")
                    continue
            
            return pending_confirmations
            
        except Exception as e:
            logger.error(f"ResumeManager: Failed to get pending confirmations: {e}")
            return []
    
    def get_confirmation(self, task_id: str) -> Optional[Dict[str, Any]]:
        """Get confirmation details for a specific task.
        
        Args:
            task_id: Task identifier
            
        Returns:
            Confirmation info or None if not found
        """
        try:
            checkpoint = self._checkpointing_service.get_latest_checkpoint(task_id)
            
            if not checkpoint:
                return None
            
            state_dict = checkpoint.workflow_state
            if not state_dict.get('paused', False):
                return None
            
            confirmation_data = state_dict.get('confirmation')
            if not confirmation_data:
                return None
            
            return {
                "task_id": task_id,
                "intervention_type": confirmation_data.get('intervention_type'),
                "reason": confirmation_data.get('reason'),
                "context": confirmation_data.get('context', {}),
                "checkpoint_identifier": checkpoint.checkpoint_identifier,
                "requested_at": confirmation_data.get('requested_at'),
                "options": confirmation_data.get('options', [])
            }
            
        except Exception as e:
            logger.error(f"ResumeManager: Failed to get confirmation for task {task_id}: {e}")
            return None
    
    def resume_workflow(
        self,
        task_id: str,
        response: str,
        response_data: Optional[Dict[str, Any]] = None
    ) -> Dict[str, Any]:
        """Resume a paused workflow with human response.
        
        This method:
        1. Loads the checkpoint for the task
        2. Restores the workflow state
        3. Applies the human response
        4. Resumes graph execution from confirmation node
        
        Args:
            task_id: Task identifier
            response: Human response (CONFIRM, DENY, etc.)
            response_data: Optional additional data from user
            
        Returns:
            Dict with resume status and final state
        """
        try:
            # Validate response type
            try:
                response_type = HumanInterventionType(response.lower())
            except ValueError:
                valid_responses = [t.value for t in HumanInterventionType]
                return {
                    "status": "error",
                    "message": f"Invalid response '{response}'. Valid responses: {valid_responses}",
                    "task_id": task_id
                }
            
            # Get latest checkpoint for task
            checkpoint = self._checkpointing_service.get_latest_checkpoint(task_id)
            
            if not checkpoint:
                return {
                    "status": "error",
                    "message": f"No checkpoint found for task {task_id}",
                    "task_id": task_id
                }
            
            # Check if state is paused
            state_dict = checkpoint.workflow_state
            if not state_dict.get('paused', False):
                return {
                    "status": "error",
                    "message": f"Task {task_id} is not paused",
                    "task_id": task_id
                }
            
            # Restore state from checkpoint
            logger.info(f"ResumeManager: Restoring state from checkpoint {checkpoint.checkpoint_identifier}")
            state = self._checkpointing_service.restore_state(checkpoint)
            
            if not state:
                return {
                    "status": "error",
                    "message": "Failed to restore state from checkpoint",
                    "task_id": task_id
                }
            
            # Apply human response
            logger.info(f"ResumeManager: Applying human response {response_type.value} to task {task_id}")
            state_update = resume_from_confirmation(state, response_type)
            
            # Add response data if provided
            if response_data and state.confirmation:
                state.confirmation.response_data = response_data
            
            # Resume graph execution
            logger.info(f"ResumeManager: Resuming graph execution for task {task_id}")
            
            try:
                from graph.graph import graph_runner
                if not graph_runner or not graph_runner.is_available():
                    return {
                        "status": "error",
                        "message": "Graph runner not available",
                        "task_id": task_id
                    }
                
                # Continue graph execution from the current state
                # We need to invoke the graph with the restored state
                # LangGraph will continue from where it left off
                final_state = graph_runner.graph.invoke(state)
                
                # Convert back to OperonixState if needed
                if isinstance(final_state, dict):
                    final_state = OperonixState(**final_state)
                
                logger.info(f"ResumeManager: Task {task_id} resumed and completed")
                
                return {
                    "status": "success",
                    "message": f"Task {task_id} resumed with response {response_type.value}",
                    "task_id": task_id,
                    "response": response_type.value,
                    "final_state": {
                        "success": final_state.final.success if final_state.final else None,
                        "response": final_state.final.response if final_state.final else None
                    }
                }
                
            except Exception as e:
                logger.error(f"ResumeManager: Failed to resume graph execution: {e}")
                return {
                    "status": "error",
                    "message": f"Failed to resume graph: {str(e)}",
                    "task_id": task_id
                }
            
        except Exception as e:
            logger.error(f"ResumeManager: Failed to resume workflow: {e}")
            return {
                "status": "error",
                "message": str(e),
                "task_id": task_id
            }
    
    def cancel_confirmation(self, task_id: str) -> Dict[str, Any]:
        """Cancel a pending confirmation and abort the workflow.
        
        Args:
            task_id: Task identifier
            
        Returns:
            Dict with cancellation status
        """
        try:
            checkpoint = self._checkpointing_service.get_latest_checkpoint(task_id)
            
            if not checkpoint:
                return {
                    "status": "error",
                    "message": f"No checkpoint found for task {task_id}",
                    "task_id": task_id
                }
            
            # Delete checkpoint
            self._checkpointing_service.delete_checkpoint(checkpoint.checkpoint_identifier)
            
            logger.info(f"ResumeManager: Cancelled confirmation for task {task_id}")
            
            return {
                "status": "success",
                "message": f"Confirmation for task {task_id} cancelled",
                "task_id": task_id
            }
            
        except Exception as e:
            logger.error(f"ResumeManager: Failed to cancel confirmation: {e}")
            return {
                "status": "error",
                "message": str(e),
                "task_id": task_id
            }


# ─── GLOBAL RESUME MANAGER INSTANCE ─────────────────────────────────────────

# Global resume manager instance (will be initialized with EventBus later)
resume_manager: Optional[ResumeManager] = None


def get_resume_manager(event_bus: Optional[Any] = None) -> ResumeManager:
    """Get or create the global resume manager instance.
    
    Args:
        event_bus: Optional EventBus instance for panel integration
        
    Returns:
        ResumeManager instance
    """
    global resume_manager
    if resume_manager is None:
        resume_manager = ResumeManager(event_bus)
    return resume_manager
