"""
Checkpointing Service — Operonix Graph
──────────────────────────────────────

Checkpointing service for persisting and resuming workflow state.
Per migration plan Phase 7: Checkpointing, Pause/Resume & Human Intervention
"""
from __future__ import annotations

import logging
import json
from pathlib import Path
from typing import Dict, Any, Optional

from migration.domain_contracts import CheckpointState
from migration.graph_state import OperonixState

logger = logging.getLogger("Graph.Checkpointing")


class CheckpointingService:
    """Service for checkpointing and resuming workflow state."""
    
    def __init__(self, checkpoint_dir: Optional[str] = None):
        """Initialize checkpointing service.
        
        Args:
            checkpoint_dir: Directory to store checkpoints. Defaults to .checkpoints/
        """
        if checkpoint_dir is None:
            checkpoint_dir = ".checkpoints"
        
        self.checkpoint_dir = Path(checkpoint_dir)
        self.checkpoint_dir.mkdir(exist_ok=True)
        
        logger.info(f"CheckpointingService initialized with directory: {self.checkpoint_dir}")
    
    def create_checkpoint(self, state: OperonixState, current_node: str) -> CheckpointState:
        """Create a checkpoint from current workflow state.
        
        Per migration plan Phase 7, persist:
        - task identity
        - workflow state
        - current node
        - current plan step
        - completed steps
        - routing decision
        - safety/confirmation state
        - execution status
        - recovery data
        - relevant context
        - state version
        - timestamp
        
        Args:
            state: Current OperonixState
            current_node: Current graph node name
            
        Returns:
            CheckpointState object
        """
        checkpoint = CheckpointState(
            task_id=state.task.task_id,
            workflow_state=state.model_dump(),
            current_node=current_node,
            current_plan_step_index=state.plan.current_step_index if state.plan else 0,
            completed_steps=state.plan.completed_steps if state.plan else [],
            routing_decision=state.routing.model_dump() if state.routing else None,
            safety_state=state.safety.model_dump() if state.safety else None,
            confirmation_state=state.confirmation.model_dump() if hasattr(state, 'confirmation') and state.confirmation else None,
            execution_status=state.execution.execution_status.value if state.execution else None,
            recovery_data=state.recovery.model_dump() if state.recovery else None,
            relevant_context=state.context.model_dump() if state.context else {}
        )
        
        # Persist checkpoint to disk
        self._persist_checkpoint(checkpoint)
        
        logger.info(f"Checkpoint created: {checkpoint.checkpoint_id} for task {state.task.task_id}")
        
        return checkpoint
    
    def _persist_checkpoint(self, checkpoint: CheckpointState) -> None:
        """Persist checkpoint to disk.
        
        Args:
            checkpoint: CheckpointState to persist
        """
        checkpoint_file = self.checkpoint_dir / f"{checkpoint.checkpoint_id}.json"
        
        with open(checkpoint_file, 'w') as f:
            json.dump(checkpoint.model_dump(), f, indent=2, default=str)
        
        logger.debug(f"Checkpoint persisted to: {checkpoint_file}")
    
    def load_checkpoint(self, checkpoint_id: str) -> Optional[CheckpointState]:
        """Load a checkpoint from disk.
        
        Args:
            checkpoint_id: Checkpoint ID to load
            
        Returns:
            CheckpointState if found, None otherwise
        """
        checkpoint_file = self.checkpoint_dir / f"{checkpoint_id}.json"
        
        if not checkpoint_file.exists():
            logger.warning(f"Checkpoint not found: {checkpoint_id}")
            return None
        
        with open(checkpoint_file, 'r') as f:
            checkpoint_data = json.load(f)
        
        checkpoint = CheckpointState(**checkpoint_data)
        
        logger.info(f"Checkpoint loaded: {checkpoint_id}")
        
        return checkpoint
    
    def restore_state(self, checkpoint: CheckpointState) -> OperonixState:
        """Restore OperonixState from checkpoint.
        
        Args:
            checkpoint: CheckpointState to restore from
            
        Returns:
            Restored OperonixState
        """
        # Reconstruct OperonixState from checkpoint workflow_state
        state_data = checkpoint.workflow_state
        state = OperonixState(**state_data)
        
        # Restore plan step index
        if state.plan:
            state.plan.current_step_index = checkpoint.current_plan_step_index
            state.plan.completed_steps = checkpoint.completed_steps
        
        logger.info(f"State restored from checkpoint: {checkpoint.checkpoint_id}")
        
        return state
    
    def get_latest_checkpoint(self, task_id: str) -> Optional[CheckpointState]:
        """Get the latest checkpoint for a task.
        
        Args:
            task_id: Task ID to get checkpoint for
            
        Returns:
            Latest CheckpointState if found, None otherwise
        """
        # Find all checkpoints for this task
        task_checkpoints = []
        for checkpoint_file in self.checkpoint_dir.glob("*.json"):
            with open(checkpoint_file, 'r') as f:
                checkpoint_data = json.load(f)
                if checkpoint_data.get("task_id") == task_id:
                    task_checkpoints.append(CheckpointState(**checkpoint_data))
        
        if not task_checkpoints:
            logger.warning(f"No checkpoints found for task: {task_id}")
            return None
        
        # Sort by timestamp and return latest
        latest_checkpoint = max(task_checkpoints, key=lambda c: c.checkpoint_timestamp)
        
        logger.info(f"Latest checkpoint for task {task_id}: {latest_checkpoint.checkpoint_id}")
        
        return latest_checkpoint
    
    def delete_checkpoint(self, checkpoint_id: str) -> bool:
        """Delete a checkpoint.
        
        Args:
            checkpoint_id: Checkpoint ID to delete
            
        Returns:
            True if deleted, False otherwise
        """
        checkpoint_file = self.checkpoint_dir / f"{checkpoint_id}.json"
        
        if not checkpoint_file.exists():
            logger.warning(f"Checkpoint not found for deletion: {checkpoint_id}")
            return False
        
        checkpoint_file.unlink()
        
        logger.info(f"Checkpoint deleted: {checkpoint_id}")
        
        return True
    
    def delete_task_checkpoints(self, task_id: str) -> int:
        """Delete all checkpoints for a task.
        
        Args:
            task_id: Task ID to delete checkpoints for
            
        Returns:
            Number of checkpoints deleted
        """
        deleted_count = 0
        
        for checkpoint_file in self.checkpoint_dir.glob("*.json"):
            with open(checkpoint_file, 'r') as f:
                checkpoint_data = json.load(f)
                if checkpoint_data.get("task_id") == task_id:
                    checkpoint_file.unlink()
                    deleted_count += 1
        
        logger.info(f"Deleted {deleted_count} checkpoints for task: {task_id}")
        
        return deleted_count


# Global checkpointing service instance
_checkpointing_service: Optional[CheckpointingService] = None


def get_checkpointing_service() -> CheckpointingService:
    """Get the global checkpointing service instance.
    
    Returns:
        CheckpointingService instance
    """
    global _checkpointing_service
    
    if _checkpointing_service is None:
        _checkpointing_service = CheckpointingService()
    
    return _checkpointing_service
