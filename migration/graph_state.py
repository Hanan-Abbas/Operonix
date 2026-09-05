"""
Graph State Schema — Operonix Migration
────────────────────────────────────────

Initial OperonixState schema for LangGraph workflow state.
This is the canonical state for a single running task.

Per migration plan §3:
- Graph state is workflow state for one running task
- NOT memory, NOT context subsystem, NOT EventBus
- NOT a place to store service objects
- State holds service RESULTS, never the objects themselves
"""
from __future__ import annotations

from typing import Any, Optional, Dict, List
from datetime import datetime
from pydantic import BaseModel, Field

from migration.domain_contracts import (
    TaskRequest,
    IntentResult,
    ContextSnapshot,
    KnowledgeContext,
    Plan,
    MethodDecision,
    SafetyDecision,
    ExecutionResult,
    VerificationResult,
    RecoveryDecision,
    ReflectionResult,
    FinalResult,
    TaskStatus,
)


class OperonixState(BaseModel):
    """Canonical workflow state for one running task.
    
    This state is managed by LangGraph and represents the complete
    workflow state for a single task execution. It does NOT include:
    - Service instances (Executor, EventBus, etc.)
    - Memory databases
    - LLM client/model objects
    - Context subsystem instances
    
    State holds the RESULTS of service calls, not the services themselves.
    """
    
    # ─── TASK IDENTITY ───────────────────────────────────────────────────────
    
    task: TaskRequest = Field(..., description="Task identity and lifecycle")
    
    # ─── INTENT ───────────────────────────────────────────────────────────────
    
    intent: Optional[IntentResult] = Field(
        default=None,
        description="Parsed intent with confidence and parameters"
    )
    
    # ─── CONTEXT ───────────────────────────────────────────────────────────────
    
    context: Optional[ContextSnapshot] = Field(
        default=None,
        description="Snapshot of environment/context at reasoning time"
    )
    
    # ─── KNOWLEDGE ────────────────────────────────────────────────────────────
    
    knowledge: Optional[KnowledgeContext] = Field(
        default=None,
        description="Retrieved memories, documents, and learned patterns"
    )
    
    # ─── PLAN ──────────────────────────────────────────────────────────────────
    
    plan: Optional[Plan] = Field(
        default=None,
        description="Execution plan with steps and progress tracking"
    )
    
    # ─── ROUTING ─────────────────────────────────────────────────────────────
    
    routing: Optional[MethodDecision] = Field(
        default=None,
        description="Routing decision with candidate evaluation"
    )
    
    # ─── SAFETY ───────────────────────────────────────────────────────────────
    
    safety: Optional[SafetyDecision] = Field(
        default=None,
        description="Safety authorization decision and constraints"
    )
    
    # ─── EXECUTION ────────────────────────────────────────────────────────────
    
    execution: Optional[ExecutionResult] = Field(
        default=None,
        description="Current step execution result"
    )
    
    # ─── HISTORY ───────────────────────────────────────────────────────────────
    
    history: Dict[str, Any] = Field(
        default_factory=dict,
        description="Workflow-run history (step_results, tool_calls, errors, events)"
    )
    
    # ─── VERIFICATION ─────────────────────────────────────────────────────────
    
    verification: Optional[VerificationResult] = Field(
        default=None,
        description="Verification of expected vs observed state"
    )
    
    # ─── RECOVERY ─────────────────────────────────────────────────────────────
    
    recovery: Optional[RecoveryDecision] = Field(
        default=None,
        description="Recovery decision for handling failures"
    )
    
    # ─── REFLECTION ───────────────────────────────────────────────────────────
    
    reflection: Optional[ReflectionResult] = Field(
        default=None,
        description="Reflection on task outcome for learning"
    )
    
    # ─── FINAL ────────────────────────────────────────────────────────────────
    
    final: Optional[FinalResult] = Field(
        default=None,
        description="Terminal result for API/Dashboard/Panel/Voice"
    )
    
    # ─── METADATA ──────────────────────────────────────────────────────────────
    
    current_node: Optional[str] = Field(
        default=None,
        description="Current graph node being executed"
    )
    
    state_version: str = Field(
        default="1.0.0",
        description="Schema version for state migration compatibility"
    )
    
    workflow_version: str = Field(
        default="1.0.0",
        description="Workflow topology version"
    )
    
    created_at: datetime = Field(
        default_factory=datetime.utcnow,
        description="When this state was created"
    )
    
    updated_at: datetime = Field(
        default_factory=datetime.utcnow,
        description="When this state was last updated"
    )
    
    class Config:
        json_encoders = {
            datetime: lambda v: v.isoformat()
        }
        arbitrary_types_allowed = True
    
    def update_timestamp(self) -> None:
        """Update the updated_at timestamp."""
        self.updated_at = datetime.utcnow()
    
    def add_history_event(self, event_type: str, data: Dict[str, Any]) -> None:
        """Add an event to the history log."""
        if "events" not in self.history:
            self.history["events"] = []
        self.history["events"].append({
            "type": event_type,
            "timestamp": datetime.utcnow().isoformat(),
            "data": data
        })
        self.update_timestamp()
    
    def get_status(self) -> TaskStatus:
        """Determine current task status based on state."""
        if self.final:
            if self.final.success:
                return TaskStatus.COMPLETED
            elif self.final.partial:
                return TaskStatus.PARTIAL
            else:
                return TaskStatus.FAILED
        
        if self.recovery:
            return TaskStatus.RECOVERING
        
        if self.safety and self.safety.confirmation_required:
            return TaskStatus.WAITING_FOR_CONFIRMATION
        
        if self.plan and self.plan.current_step:
            return TaskStatus.RUNNING
        
        return TaskStatus.PENDING


# ─── CHECKPOINT STATE ─────────────────────────────────────────────────────────

class CheckpointState(BaseModel):
    """Persisted checkpoint for workflow resume.
    
    Contains enough information to resume a workflow after interruption.
    Per migration plan §9.3, checkpoint should contain:
    - task identity
    - workflow state
    - current graph/node position
    - current plan step
    - completed steps
    - routing decision
    - safety/confirmation state
    - execution status
    - retry/recovery information
    - relevant context snapshot
    - state version
    - checkpoint timestamp
    """
    
    checkpoint_id: str = Field(default_factory=lambda: str(uuid.uuid4()))
    task_id: str
    workflow_state: OperonixState
    current_node: Optional[str] = None
    current_step_index: int = 0
    completed_steps: List[str] = Field(default_factory=list)
    routing_decision: Optional[MethodDecision] = None
    safety_state: Optional[SafetyDecision] = None
    execution_status: Optional[TaskStatus] = None
    recovery_data: Dict[str, Any] = Field(default_factory=dict)
    context_snapshot: Optional[ContextSnapshot] = None
    state_schema_version: str = "1.0.0"
    workflow_version: str = "1.0.0"
    checkpoint_timestamp: datetime = Field(default_factory=datetime.utcnow)
    
    class Config:
        json_encoders = {
            datetime: lambda v: v.isoformat()
        }


import uuid
