"""
Trace Collector — Operonix Graph
────────────────────────────────

Trace collector for execution trace collection.
Per migration plan Phase 9: Observability & Execution Trace

The trace should expose: request, intent, context, retrieved knowledge, plan,
routing candidates, routing decision, safety decision, execution attempts,
observations, verification, recovery, reflection, final outcome.
"""
from __future__ import annotations

import logging
from typing import Dict, Any, Optional, List
from datetime import datetime

from migration.domain_contracts import TraceEvent, TraceEventType, ExecutionTrace
from migration.graph_state import OperonixState

logger = logging.getLogger("Graph.TraceCollector")


class TraceCollector:
    """Collector for execution trace events.
    
    Per migration plan Phase 9: For a failed task, a developer can reconstruct:
    - what happened
    - why it happened
    - what was selected
    - why alternatives were rejected
    - what failed
    - why recovery occurred
    - what final result was produced
    """
    
    def __init__(self):
        """Initialize trace collector."""
        self.active_traces: Dict[str, ExecutionTrace] = {}
        
        logger.info("TraceCollector initialized")
    
    def start_trace(self, task_id: str) -> ExecutionTrace:
        """Start a new execution trace for a task.
        
        Args:
            task_id: Task identifier
            
        Returns:
            ExecutionTrace
        """
        trace = ExecutionTrace(task_id=task_id)
        self.active_traces[task_id] = trace
        
        logger.info(f"Started trace for task: {task_id}")
        
        return trace
    
    def end_trace(self, task_id: str, success: bool, final_outcome: str) -> ExecutionTrace:
        """End an execution trace.
        
        Args:
            task_id: Task identifier
            success: Whether the task succeeded
            final_outcome: Final outcome description
            
        Returns:
            ExecutionTrace
        """
        if task_id not in self.active_traces:
            logger.warning(f"No active trace found for task: {task_id}")
            return None
        
        trace = self.active_traces[task_id]
        trace.completed_at = datetime.utcnow()
        trace.success = success
        trace.final_outcome = final_outcome
        
        logger.info(f"Ended trace for task: {task_id} (success: {success})")
        
        return trace
    
    def add_trace_event(self, task_id: str, event_type: TraceEventType, data: Dict[str, Any], node_name: Optional[str] = None) -> Optional[TraceEvent]:
        """Add a trace event to the execution trace.
        
        Args:
            task_id: Task identifier
            event_type: Type of trace event
            data: Event data
            node_name: Graph node that generated this event
            
        Returns:
            TraceEvent if trace exists, None otherwise
        """
        if task_id not in self.active_traces:
            logger.warning(f"No active trace found for task: {task_id}")
            return None
        
        trace = self.active_traces[task_id]
        event = TraceEvent(
            task_id=task_id,
            event_type=event_type,
            data=data,
            node_name=node_name
        )
        
        trace.add_event(event)
        
        logger.debug(f"Added trace event: {event_type.value} for task {task_id}")
        
        return event
    
    def get_trace(self, task_id: str) -> Optional[ExecutionTrace]:
        """Get the execution trace for a task.
        
        Args:
            task_id: Task identifier
            
        Returns:
            ExecutionTrace if found, None otherwise
        """
        return self.active_traces.get(task_id)
    
    def collect_request(self, task_id: str, user_input: str, source: str) -> None:
        """Collect request event.
        
        Args:
            task_id: Task identifier
            user_input: User input
            source: Request source
        """
        self.add_trace_event(
            task_id=task_id,
            event_type=TraceEventType.REQUEST,
            data={
                "user_input": user_input,
                "source": source
            },
            node_name="intake"
        )
    
    def collect_intent(self, task_id: str, intent_name: str, intent_parameters: Dict[str, Any]) -> None:
        """Collect intent event.
        
        Args:
            task_id: Task identifier
            intent_name: Intent name
            intent_parameters: Intent parameters
        """
        self.add_trace_event(
            task_id=task_id,
            event_type=TraceEventType.INTENT,
            data={
                "intent_name": intent_name,
                "intent_parameters": intent_parameters
            },
            node_name="analyze_intent"
        )
    
    def collect_context(self, task_id: str, context_data: Dict[str, Any]) -> None:
        """Collect context event.
        
        Args:
            task_id: Task identifier
            context_data: Context data
        """
        self.add_trace_event(
            task_id=task_id,
            event_type=TraceEventType.CONTEXT,
            data=context_data,
            node_name="observe"
        )
    
    def collect_retrieved_knowledge(self, task_id: str, knowledge_data: Dict[str, Any]) -> None:
        """Collect retrieved knowledge event.
        
        Args:
            task_id: Task identifier
            knowledge_data: Retrieved knowledge data
        """
        self.add_trace_event(
            task_id=task_id,
            event_type=TraceEventType.RETRIEVED_KNOWLEDGE,
            data=knowledge_data,
            node_name="retrieve_knowledge"
        )
    
    def collect_plan(self, task_id: str, plan_data: Dict[str, Any]) -> None:
        """Collect plan event.
        
        Args:
            task_id: Task identifier
            plan_data: Plan data
        """
        self.add_trace_event(
            task_id=task_id,
            event_type=TraceEventType.PLAN,
            data=plan_data,
            node_name="create_plan"
        )
    
    def collect_routing_candidates(self, task_id: str, candidates: List[Dict[str, Any]]) -> None:
        """Collect routing candidates event.
        
        Args:
            task_id: Task identifier
            candidates: Routing candidates
        """
        self.add_trace_event(
            task_id=task_id,
            event_type=TraceEventType.ROUTING_CANDIDATES,
            data={"candidates": candidates},
            node_name="route"
        )
    
    def collect_routing_decision(self, task_id: str, decision: Dict[str, Any]) -> None:
        """Collect routing decision event.
        
        Args:
            task_id: Task identifier
            decision: Routing decision
        """
        self.add_trace_event(
            task_id=task_id,
            event_type=TraceEventType.ROUTING_DECISION,
            data=decision,
            node_name="route"
        )
    
    def collect_safety_decision(self, task_id: str, safety_data: Dict[str, Any]) -> None:
        """Collect safety decision event.
        
        Args:
            task_id: Task identifier
            safety_data: Safety decision data
        """
        self.add_trace_event(
            task_id=task_id,
            event_type=TraceEventType.SAFETY_DECISION,
            data=safety_data,
            node_name="safety_check"
        )
    
    def collect_execution_attempt(self, task_id: str, execution_data: Dict[str, Any]) -> None:
        """Collect execution attempt event.
        
        Args:
            task_id: Task identifier
            execution_data: Execution data
        """
        self.add_trace_event(
            task_id=task_id,
            event_type=TraceEventType.EXECUTION_ATTEMPT,
            data=execution_data,
            node_name="execute_step"
        )
    
    def collect_observation(self, task_id: str, observation_data: Dict[str, Any]) -> None:
        """Collect observation event.
        
        Args:
            task_id: Task identifier
            observation_data: Observation data
        """
        self.add_trace_event(
            task_id=task_id,
            event_type=TraceEventType.OBSERVATION,
            data=observation_data,
            node_name="observe"
        )
    
    def collect_verification(self, task_id: str, verification_data: Dict[str, Any]) -> None:
        """Collect verification event.
        
        Args:
            task_id: Task identifier
            verification_data: Verification data
        """
        self.add_trace_event(
            task_id=task_id,
            event_type=TraceEventType.VERIFICATION,
            data=verification_data,
            node_name="verify_step"
        )
    
    def collect_recovery(self, task_id: str, recovery_data: Dict[str, Any]) -> None:
        """Collect recovery event.
        
        Args:
            task_id: Task identifier
            recovery_data: Recovery data
        """
        self.add_trace_event(
            task_id=task_id,
            event_type=TraceEventType.RECOVERY,
            data=recovery_data,
            node_name="recover"
        )
    
    def collect_reflection(self, task_id: str, reflection_data: Dict[str, Any]) -> None:
        """Collect reflection event.
        
        Args:
            task_id: Task identifier
            reflection_data: Reflection data
        """
        self.add_trace_event(
            task_id=task_id,
            event_type=TraceEventType.REFLECTION,
            data=reflection_data,
            node_name="reflect"
        )
    
    def collect_final_outcome(self, task_id: str, outcome_data: Dict[str, Any]) -> None:
        """Collect final outcome event.
        
        Args:
            task_id: Task identifier
            outcome_data: Final outcome data
        """
        self.add_trace_event(
            task_id=task_id,
            event_type=TraceEventType.FINAL_OUTCOME,
            data=outcome_data,
            node_name="finalize"
        )


# Global trace collector instance
_trace_collector: Optional[TraceCollector] = None


def get_trace_collector() -> TraceCollector:
    """Get the global trace collector instance.
    
    Returns:
        TraceCollector instance
    """
    global _trace_collector
    
    if _trace_collector is None:
        _trace_collector = TraceCollector()
    
    return _trace_collector
