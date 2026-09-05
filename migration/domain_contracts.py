"""
Domain Contracts — Operonix Migration
──────────────────────────────────────

Shared domain contracts (data classes, typed interfaces) that will be used
across the migration boundary. These contracts ensure type safety and
clear interfaces between legacy code, graph nodes, and services.

All contracts use Pydantic for validation and serialization.
"""
from __future__ import annotations

from typing import Any, Optional, Literal, Dict, List
from datetime import datetime
from enum import Enum
from pydantic import BaseModel, Field


# ─── TASK LIFECYCLE ───────────────────────────────────────────────────────────

class TaskStatus(str, Enum):
    """Status of a task in the workflow."""
    PENDING = "pending"
    RUNNING = "running"
    PAUSED = "paused"
    WAITING_FOR_CONFIRMATION = "waiting_for_confirmation"
    RECOVERING = "recovering"
    COMPLETED = "completed"
    PARTIAL = "partial"
    FAILED = "failed"
    CANCELLED = "cancelled"
    ABORTED = "aborted"
    TIMED_OUT = "timed_out"


class TaskSource(str, Enum):
    """Source of a task request."""
    VOICE = "voice"
    PANEL = "panel"
    API = "api"
    CLI = "cli"


# ─── TASK REQUEST ─────────────────────────────────────────────────────────────

class TaskRequest(BaseModel):
    """Initial request to create a task."""
    task_id: str = Field(default_factory=lambda: str(uuid.uuid4()))
    user_input: str
    source: TaskSource
    created_at: datetime = Field(default_factory=datetime.utcnow)
    metadata: dict[str, Any] = Field(default_factory=dict)
    
    class Config:
        json_encoders = {
            datetime: lambda v: v.isoformat()
        }


# ─── INTENT ───────────────────────────────────────────────────────────────────

class IntentResult(BaseModel):
    """Result of intent parsing/analysis."""
    name: str
    confidence: float = Field(ge=0.0, le=1.0)
    parameters: dict[str, Any] = Field(default_factory=dict)
    profile_hint: Optional[str] = None
    raw_intent: Optional[str] = None
    fallback_used: bool = False
    risk_hint: Optional[str] = None


# ─── CONTEXT ─────────────────────────────────────────────────────────────────

class ContextSnapshot(BaseModel):
    """Snapshot of the current environment/context."""
    active_window: Optional[str] = None
    app: Optional[str] = None
    app_type: Optional[str] = None
    window_title: Optional[str] = None
    cwd: Optional[str] = None
    sub_context: Optional[str] = None
    ui_state: dict[str, Any] = Field(default_factory=dict)
    permissions: list[str] = Field(default_factory=list)
    confidence: float = Field(default=0.0, ge=0.0, le=1.0)
    captured_at: datetime = Field(default_factory=datetime.utcnow)
    
    class Config:
        json_encoders = {
            datetime: lambda v: v.isoformat()
        }


# ─── KNOWLEDGE ────────────────────────────────────────────────────────────────

class KnowledgeContext(BaseModel):
    """Retrieved knowledge from memory/RAG systems."""
    retrieved_memories: list[dict[str, Any]] = Field(default_factory=list)
    retrieved_documents: list[dict[str, Any]] = Field(default_factory=list)
    learned_patterns: list[dict[str, Any]] = Field(default_factory=list)
    provenance: dict[str, Any] = Field(default_factory=dict)


# ─── PLANNING ─────────────────────────────────────────────────────────────────

class PlanStep(BaseModel):
    """A single step in an execution plan."""
    step_id: str
    action: str
    arguments: dict[str, Any] = Field(default_factory=dict)
    objective: Optional[str] = None
    expected_outcome: Optional[str] = None
    dependencies: list[str] = Field(default_factory=list)
    metadata: dict[str, Any] = Field(default_factory=dict)
    
    # Idempotency and side-effect classification
    idempotency: Literal["SAFE", "CONDITIONAL", "NON_IDEMPOTENT"] = "CONDITIONAL"
    side_effect: Literal["READ_ONLY", "REVERSIBLE", "LIMITED_SIDE_EFFECT", "DESTRUCTIVE", "EXTERNAL_COMMIT"] = "LIMITED_SIDE_EFFECT"
    reversibility: bool = False
    preconditions: list[str] = Field(default_factory=list)
    postconditions: list[str] = Field(default_factory=list)
    
    # Execution policy
    retry_policy: dict[str, Any] = Field(default_factory=dict)
    timeout: Optional[int] = None


class Plan(BaseModel):
    """Complete execution plan for a task."""
    plan_id: str = Field(default_factory=lambda: str(uuid.uuid4()))
    steps: list[PlanStep]
    current_step_index: int = 0
    completed_steps: list[str] = Field(default_factory=list)
    created_at: datetime = Field(default_factory=datetime.utcnow)
    
    class Config:
        json_encoders = {
            datetime: lambda v: v.isoformat()
        }
    
    @property
    def current_step(self) -> Optional[PlanStep]:
        """Get the current step to execute."""
        if 0 <= self.current_step_index < len(self.steps):
            return self.steps[self.current_step_index]
        return None
    
    @property
    def is_complete(self) -> bool:
        """Check if all steps are completed."""
        return self.current_step_index >= len(self.steps)


# ─── ROUTING ─────────────────────────────────────────────────────────────────

class RoutingCandidate(BaseModel):
    """A candidate execution method for a step."""
    method_type: str  # PLUGIN, API, SHELL, UI, etc.
    tool_id: Optional[str] = None
    capability_id: Optional[str] = None
    plugin_id: Optional[str] = None
    
    # Evaluation scores
    capability_fit: float = Field(default=0.0, ge=0.0, le=1.0)
    context_fit: float = Field(default=0.0, ge=0.0, le=1.0)
    availability: float = Field(default=0.0, ge=0.0, le=1.0)
    reliability: float = Field(default=0.0, ge=0.0, le=1.0)
    historical_success: float = Field(default=0.0, ge=0.0, le=1.0)
    risk: float = Field(default=0.0, ge=0.0, le=1.0)
    permissions: float = Field(default=0.0, ge=0.0, le=1.0)
    latency: Optional[float] = None
    reversibility: float = Field(default=0.0, ge=0.0, le=1.0)
    
    # Overall score (computed by routing engine)
    overall_score: float = Field(default=0.0, ge=0.0, le=1.0)
    
    # Decision metadata
    rejection_reason: Optional[str] = None
    policy_constraints: list[str] = Field(default_factory=list)
    fallback_position: Optional[int] = None


class MethodDecision(BaseModel):
    """Decision on which execution method to use."""
    selected_candidate: RoutingCandidate
    confidence: float = Field(ge=0.0, le=1.0)
    candidates_considered: list[RoutingCandidate] = Field(default_factory=list)
    rejected_candidates: list[RoutingCandidate] = Field(default_factory=list)
    fallback_candidates: list[RoutingCandidate] = Field(default_factory=list)
    policy_decision: Optional[str] = None
    safety_constraints: list[str] = Field(default_factory=list)
    routing_explanation: Optional[str] = None
    decision_timestamp: datetime = Field(default_factory=datetime.utcnow)
    
    class Config:
        json_encoders = {
            datetime: lambda v: v.isoformat()
        }


# ─── SAFETY ───────────────────────────────────────────────────────────────────

class RiskLevel(str, Enum):
    """Risk level classification."""
    SAFE = "safe"
    LOW = "low"
    MEDIUM = "medium"
    HIGH = "high"
    CRITICAL = "critical"


class SafetyDecision(BaseModel):
    """Safety authorization decision."""
    risk_level: RiskLevel
    validation_status: Literal["APPROVED", "REJECTED", "REQUIRES_CONFIRMATION"]
    permission_status: Literal["GRANTED", "DENIED", "UNKNOWN"]
    confirmation_required: bool = False
    confirmation_reason: Optional[str] = None
    user_decision: Optional[Literal["ALLOW", "DENY"]] = None
    policy_constraints: list[str] = Field(default_factory=list)
    safety_checks_performed: list[str] = Field(default_factory=list)
    decision_timestamp: datetime = Field(default_factory=datetime.utcnow)
    
    class Config:
        json_encoders = {
            datetime: lambda v: v.isoformat()
        }


# ─── EXECUTION ───────────────────────────────────────────────────────────────

class ExecutionRequest(BaseModel):
    """Request to execute a step."""
    step: PlanStep
    method_decision: MethodDecision
    context_snapshot: ContextSnapshot
    execution_id: str = Field(default_factory=lambda: str(uuid.uuid4()))


class ExecutionResult(BaseModel):
    """Result of executing a step."""
    execution_id: str
    step_id: str
    success: bool
    result_data: dict[str, Any] = Field(default_factory=dict)
    error: Optional[str] = None
    error_type: Optional[str] = None
    attempt: int = 1
    method_used: str
    execution_status: TaskStatus
    started_at: datetime = Field(default_factory=datetime.utcnow)
    completed_at: Optional[datetime] = None
    
    class Config:
        json_encoders = {
            datetime: lambda v: v.isoformat()
        }


# ─── VERIFICATION ────────────────────────────────────────────────────────────

class VerificationResult(BaseModel):
    """Result of verifying that an execution produced the expected outcome."""
    status: Literal["VERIFIED", "FAILED", "UNCERTAIN"]
    observed_context: ContextSnapshot
    expected_state: dict[str, Any] = Field(default_factory=dict)
    actual_state: dict[str, Any] = Field(default_factory=dict)
    reason: Optional[str] = None
    verification_timestamp: datetime = Field(default_factory=datetime.utcnow)
    
    class Config:
        json_encoders = {
            datetime: lambda v: v.isoformat()
        }


# ─── RECOVERY ────────────────────────────────────────────────────────────────

class FailureCategory(str, Enum):
    """Classification of failure types."""
    TRANSIENT = "transient"
    PERMANENT = "permanent"
    ENVIRONMENTAL = "environmental"
    CONTEXT_MISMATCH = "context_mismatch"
    ROUTING_MISMATCH = "routing_mismatch"
    TOOL_UNAVAILABLE = "tool_unavailable"
    PERMISSION_DENIED = "permission_denied"
    VALIDATION_REJECTED = "validation_rejected"
    PLANNING_ERROR = "planning_error"
    UNKNOWN = "unknown"


class RecoveryStrategy(str, Enum):
    """Recovery strategy to apply."""
    RETRY = "retry"
    OBSERVE = "observe"
    ROUTE = "route"
    REPLAN = "replan"
    ABORT = "abort"
    MANUAL_INTERVENTION = "manual_intervention"


class RecoveryDecision(BaseModel):
    """Decision on how to recover from a failure."""
    failure_category: FailureCategory
    recovery_strategy: RecoveryStrategy
    retry_count: int = 0
    fallback_used: bool = False
    replan_required: bool = False
    target_stage: Optional[str] = None
    reason: Optional[str] = None
    decision_timestamp: datetime = Field(default_factory=datetime.utcnow)
    
    class Config:
        json_encoders = {
            datetime: lambda v: v.isoformat()
        }


# ─── REFLECTION ────────────────────────────────────────────────────────────

class OutcomeGrade(str, Enum):
    """Grade of task outcome."""
    EXCELLENT = "excellent"
    GOOD = "good"
    ACCEPTABLE = "acceptable"
    POOR = "poor"
    FAILED = "failed"


class ReflectionResult(BaseModel):
    """Result of reflection on task execution."""
    outcome: OutcomeGrade
    failure_category: Optional[FailureCategory] = None
    lesson: Optional[str] = None
    confidence_delta: float = Field(default=0.0)
    evolution_needed: bool = False
    reflection_timestamp: datetime = Field(default_factory=datetime.utcnow)
    
    class Config:
        json_encoders = {
            datetime: lambda v: v.isoformat()
        }


# ─── FINAL RESULT ───────────────────────────────────────────────────────────

class FinalResult(BaseModel):
    """Terminal result reported to user/API/Dashboard/Voice."""
    success: bool
    partial: bool = False
    response: str
    error: Optional[str] = None
    task_id: str
    completed_at: datetime = Field(default_factory=datetime.utcnow)
    
    class Config:
        json_encoders = {
            datetime: lambda v: v.isoformat()
        }


# Import uuid for default factories
import uuid
