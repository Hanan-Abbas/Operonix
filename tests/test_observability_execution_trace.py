"""
Observability & Execution Trace Tests — Operonix Migration Phase 9
────────────────────────────────────────────────────────────────────

Tests for observability and execution trace.
Per migration plan Phase 9: Observability & Execution Trace
"""
from __future__ import annotations

import pytest


# ─── TRACE EVENT TYPE TESTS ───────────────────────────────────────────────────

def test_trace_event_type_enum():
    """Test that TraceEventType enum has all required values."""
    from migration.domain_contracts import TraceEventType
    
    assert TraceEventType.REQUEST.value == "request"
    assert TraceEventType.INTENT.value == "intent"
    assert TraceEventType.CONTEXT.value == "context"
    assert TraceEventType.RETRIEVED_KNOWLEDGE.value == "retrieved_knowledge"
    assert TraceEventType.PLAN.value == "plan"
    assert TraceEventType.ROUTING_CANDIDATES.value == "routing_candidates"
    assert TraceEventType.ROUTING_DECISION.value == "routing_decision"
    assert TraceEventType.SAFETY_DECISION.value == "safety_decision"
    assert TraceEventType.EXECUTION_ATTEMPT.value == "execution_attempt"
    assert TraceEventType.OBSERVATION.value == "observation"
    assert TraceEventType.VERIFICATION.value == "verification"
    assert TraceEventType.RECOVERY.value == "recovery"
    assert TraceEventType.REFLECTION.value == "reflection"
    assert TraceEventType.FINAL_OUTCOME.value == "final_outcome"


# ─── TRACE EVENT TESTS ───────────────────────────────────────────────────────

def test_trace_event_domain_object():
    """Test that TraceEvent is a valid domain object."""
    from migration.domain_contracts import TraceEvent, TraceEventType
    
    event = TraceEvent(
        task_id="test_task",
        event_type=TraceEventType.REQUEST,
        data={"user_input": "Open Firefox"}
    )
    
    assert event.task_id == "test_task"
    assert event.event_type == TraceEventType.REQUEST
    assert event.event_id is not None
    assert event.timestamp is not None


def test_trace_event_with_node_name():
    """Test that TraceEvent can have node_name."""
    from migration.domain_contracts import TraceEvent, TraceEventType
    
    event = TraceEvent(
        task_id="test_task",
        event_type=TraceEventType.REQUEST,
        data={"user_input": "Open Firefox"},
        node_name="intake"
    )
    
    assert event.node_name == "intake"


# ─── OBSERVABILITY EVENT TESTS ───────────────────────────────────────────────

def test_observability_event_domain_object():
    """Test that ObservabilityEvent is a valid domain object."""
    from migration.domain_contracts import ObservabilityEvent
    
    event = ObservabilityEvent(
        task_id="test_task",
        event_type="node_started",
        data={"node": "intake"}
    )
    
    assert event.task_id == "test_task"
    assert event.event_type == "node_started"
    assert event.event_id is not None
    assert event.timestamp is not None


def test_observability_event_with_metadata():
    """Test that ObservabilityEvent can have metadata."""
    from migration.domain_contracts import ObservabilityEvent
    
    event = ObservabilityEvent(
        task_id="test_task",
        event_type="node_started",
        data={"node": "intake"},
        metadata={"duration_ms": 100}
    )
    
    assert event.metadata == {"duration_ms": 100}


# ─── EXECUTION TRACE TESTS ───────────────────────────────────────────────────

def test_execution_trace_domain_object():
    """Test that ExecutionTrace is a valid domain object."""
    from migration.domain_contracts import ExecutionTrace, TraceEvent, TraceEventType
    
    trace = ExecutionTrace(task_id="test_task")
    
    assert trace.task_id == "test_task"
    assert trace.trace_id is not None
    assert trace.started_at is not None
    assert trace.completed_at is None
    assert trace.success is False
    assert len(trace.events) == 0


def test_execution_trace_add_event():
    """Test that events can be added to execution trace."""
    from migration.domain_contracts import ExecutionTrace, TraceEvent, TraceEventType
    
    trace = ExecutionTrace(task_id="test_task")
    event = TraceEvent(
        task_id="test_task",
        event_type=TraceEventType.REQUEST,
        data={"user_input": "Open Firefox"}
    )
    
    trace.add_event(event)
    
    assert len(trace.events) == 1
    assert trace.events[0].event_type == TraceEventType.REQUEST


def test_execution_trace_get_events_by_type():
    """Test that events can be filtered by type."""
    from migration.domain_contracts import ExecutionTrace, TraceEvent, TraceEventType
    
    trace = ExecutionTrace(task_id="test_task")
    trace.add_event(TraceEvent(task_id="test_task", event_type=TraceEventType.REQUEST, data={}))
    trace.add_event(TraceEvent(task_id="test_task", event_type=TraceEventType.INTENT, data={}))
    trace.add_event(TraceEvent(task_id="test_task", event_type=TraceEventType.REQUEST, data={}))
    
    request_events = trace.get_events_by_type(TraceEventType.REQUEST)
    
    assert len(request_events) == 2


def test_execution_trace_reconstruct_timeline():
    """Test that timeline can be reconstructed from trace."""
    from migration.domain_contracts import ExecutionTrace, TraceEvent, TraceEventType
    
    trace = ExecutionTrace(task_id="test_task")
    trace.add_event(TraceEvent(task_id="test_task", event_type=TraceEventType.REQUEST, data={}, node_name="intake"))
    trace.add_event(TraceEvent(task_id="test_task", event_type=TraceEventType.INTENT, data={}, node_name="analyze_intent"))
    trace.add_event(TraceEvent(task_id="test_task", event_type=TraceEventType.PLAN, data={}, node_name="create_plan"))
    
    timeline = trace.reconstruct_timeline()
    
    assert len(timeline) == 3
    assert timeline[0]["event_type"] == "request"
    assert timeline[1]["event_type"] == "intent"
    assert timeline[2]["event_type"] == "plan"


def test_execution_trace_end_trace():
    """Test that trace can be ended."""
    from migration.domain_contracts import ExecutionTrace
    
    trace = ExecutionTrace(task_id="test_task")
    trace.end_trace(task_id="test_task", success=True, final_outcome="Task completed")
    
    assert trace.completed_at is not None
    assert trace.success is True
    assert trace.final_outcome == "Task completed"


# ─── EVENT BUS TESTS ───────────────────────────────────────────────────────

def test_event_bus_init():
    """Test that EventBus can be initialized."""
    from graph.event_bus import EventBus
    
    bus = EventBus()
    assert bus is not None
    assert len(bus.subscribers) == 0
    assert len(bus.event_history) == 0


def test_event_bus_subscribe():
    """Test that subscribers can subscribe to events."""
    from graph.event_bus import EventBus
    from migration.domain_contracts import ObservabilityEvent
    
    bus = EventBus()
    
    callback_called = []
    def callback(event):
        callback_called.append(event)
    
    bus.subscribe("node_started", callback)
    
    assert "node_started" in bus.subscribers
    assert len(bus.subscribers["node_started"]) == 1


def test_event_bus_publish():
    """Test that events can be published."""
    from graph.event_bus import EventBus
    from migration.domain_contracts import ObservabilityEvent
    
    bus = EventBus()
    
    callback_called = []
    def callback(event):
        callback_called.append(event)
    
    bus.subscribe("node_started", callback)
    
    event = ObservabilityEvent(
        task_id="test_task",
        event_type="node_started",
        data={"node": "intake"}
    )
    
    bus.publish(event)
    
    assert len(callback_called) == 1
    assert callback_called[0].event_type == "node_started"
    assert len(bus.event_history) == 1


def test_event_bus_create_and_publish():
    """Test that events can be created and published in one call."""
    from graph.event_bus import EventBus
    
    bus = EventBus()
    
    callback_called = []
    def callback(event):
        callback_called.append(event)
    
    bus.subscribe("node_started", callback)
    
    event = bus.create_and_publish(
        task_id="test_task",
        event_type="node_started",
        data={"node": "intake"}
    )
    
    assert event.task_id == "test_task"
    assert len(callback_called) == 1


def test_event_bus_get_event_history():
    """Test that event history can be retrieved."""
    from graph.event_bus import EventBus
    from migration.domain_contracts import ObservabilityEvent
    
    bus = EventBus()
    
    bus.create_and_publish(task_id="task_1", event_type="node_started", data={})
    bus.create_and_publish(task_id="task_2", event_type="node_started", data={})
    bus.create_and_publish(task_id="task_1", event_type="node_completed", data={})
    
    all_events = bus.get_event_history()
    assert len(all_events) == 3
    
    task_1_events = bus.get_event_history(task_id="task_1")
    assert len(task_1_events) == 2
    
    node_started_events = bus.get_event_history(event_type="node_started")
    assert len(node_started_events) == 2


def test_event_bus_clear_history():
    """Test that event history can be cleared."""
    from graph.event_bus import EventBus
    
    bus = EventBus()
    
    bus.create_and_publish(task_id="task_1", event_type="node_started", data={})
    bus.create_and_publish(task_id="task_2", event_type="node_started", data={})
    
    cleared_count = bus.clear_history()
    assert cleared_count == 2
    assert len(bus.event_history) == 0


def test_event_bus_clear_history_for_task():
    """Test that event history can be cleared for a specific task."""
    from graph.event_bus import EventBus
    
    bus = EventBus()
    
    bus.create_and_publish(task_id="task_1", event_type="node_started", data={})
    bus.create_and_publish(task_id="task_2", event_type="node_started", data={})
    bus.create_and_publish(task_id="task_1", event_type="node_completed", data={})
    
    cleared_count = bus.clear_history(task_id="task_1")
    assert cleared_count == 2
    assert len(bus.event_history) == 1


def test_event_bus_unsubscribe():
    """Test that subscribers can unsubscribe."""
    from graph.event_bus import EventBus
    
    bus = EventBus()
    
    callback_called = []
    def callback(event):
        callback_called.append(event)
    
    bus.subscribe("node_started", callback)
    unsubscribed = bus.unsubscribe("node_started", callback)
    
    assert unsubscribed is True
    assert len(bus.subscribers["node_started"]) == 0


# ─── TRACE COLLECTOR TESTS ───────────────────────────────────────────────────

def test_trace_collector_init():
    """Test that TraceCollector can be initialized."""
    from graph.trace_collector import TraceCollector
    
    collector = TraceCollector()
    assert collector is not None
    assert len(collector.active_traces) == 0


def test_trace_collector_start_trace():
    """Test that trace can be started."""
    from graph.trace_collector import TraceCollector
    
    collector = TraceCollector()
    trace = collector.start_trace("test_task")
    
    assert trace is not None
    assert trace.task_id == "test_task"
    assert "test_task" in collector.active_traces


def test_trace_collector_end_trace():
    """Test that trace can be ended."""
    from graph.trace_collector import TraceCollector
    
    collector = TraceCollector()
    collector.start_trace("test_task")
    
    trace = collector.end_trace("test_task", success=True, final_outcome="Task completed")
    
    assert trace.success is True
    assert trace.final_outcome == "Task completed"
    assert trace.completed_at is not None


def test_trace_collector_add_trace_event():
    """Test that trace event can be added."""
    from graph.trace_collector import TraceCollector
    from migration.domain_contracts import TraceEventType
    
    collector = TraceCollector()
    collector.start_trace("test_task")
    
    event = collector.add_trace_event(
        task_id="test_task",
        event_type=TraceEventType.REQUEST,
        data={"user_input": "Open Firefox"},
        node_name="intake"
    )
    
    assert event is not None
    assert event.event_type == TraceEventType.REQUEST


def test_trace_collector_get_trace():
    """Test that trace can be retrieved."""
    from graph.trace_collector import TraceCollector
    
    collector = TraceCollector()
    collector.start_trace("test_task")
    
    trace = collector.get_trace("test_task")
    
    assert trace is not None
    assert trace.task_id == "test_task"


def test_trace_collector_collect_request():
    """Test that request event can be collected."""
    from graph.trace_collector import TraceCollector
    
    collector = TraceCollector()
    collector.start_trace("test_task")
    
    collector.collect_request(
        task_id="test_task",
        user_input="Open Firefox",
        source="voice"
    )
    
    trace = collector.get_trace("test_task")
    assert len(trace.events) == 1


def test_trace_collector_collect_intent():
    """Test that intent event can be collected."""
    from graph.trace_collector import TraceCollector
    
    collector = TraceCollector()
    collector.start_trace("test_task")
    
    collector.collect_intent(
        task_id="test_task",
        intent_name="open_application",
        intent_parameters={"application": "firefox"}
    )
    
    trace = collector.get_trace("test_task")
    assert len(trace.events) == 1


def test_trace_collector_collect_context():
    """Test that context event can be collected."""
    from graph.trace_collector import TraceCollector
    
    collector = TraceCollector()
    collector.start_trace("test_task")
    
    collector.collect_context(
        task_id="test_task",
        context_data={"window": "Firefox"}
    )
    
    trace = collector.get_trace("test_task")
    assert len(trace.events) == 1


def test_trace_collector_collect_retrieved_knowledge():
    """Test that retrieved knowledge event can be collected."""
    from graph.trace_collector import TraceCollector
    
    collector = TraceCollector()
    collector.start_trace("test_task")
    
    collector.collect_retrieved_knowledge(
        task_id="test_task",
        knowledge_data={"num_memories": 5}
    )
    
    trace = collector.get_trace("test_task")
    assert len(trace.events) == 1


def test_trace_collector_collect_plan():
    """Test that plan event can be collected."""
    from graph.trace_collector import TraceCollector
    
    collector = TraceCollector()
    collector.start_trace("test_task")
    
    collector.collect_plan(
        task_id="test_task",
        plan_data={"num_steps": 3}
    )
    
    trace = collector.get_trace("test_task")
    assert len(trace.events) == 1


def test_trace_collector_collect_routing_candidates():
    """Test that routing candidates event can be collected."""
    from graph.trace_collector import TraceCollector
    
    collector = TraceCollector()
    collector.start_trace("test_task")
    
    collector.collect_routing_candidates(
        task_id="test_task",
        candidates=[{"method_type": "SHELL", "score": 0.8}]
    )
    
    trace = collector.get_trace("test_task")
    assert len(trace.events) == 1


def test_trace_collector_collect_routing_decision():
    """Test that routing decision event can be collected."""
    from graph.trace_collector import TraceCollector
    
    collector = TraceCollector()
    collector.start_trace("test_task")
    
    collector.collect_routing_decision(
        task_id="test_task",
        decision={"selected_method": "SHELL"}
    )
    
    trace = collector.get_trace("test_task")
    assert len(trace.events) == 1


def test_trace_collector_collect_safety_decision():
    """Test that safety decision event can be collected."""
    from graph.trace_collector import TraceCollector
    
    collector = TraceCollector()
    collector.start_trace("test_task")
    
    collector.collect_safety_decision(
        task_id="test_task",
        safety_data={"risk_level": "LOW"}
    )
    
    trace = collector.get_trace("test_task")
    assert len(trace.events) == 1


def test_trace_collector_collect_execution_attempt():
    """Test that execution attempt event can be collected."""
    from graph.trace_collector import TraceCollector
    
    collector = TraceCollector()
    collector.start_trace("test_task")
    
    collector.collect_execution_attempt(
        task_id="test_task",
        execution_data={"method_used": "SHELL"}
    )
    
    trace = collector.get_trace("test_task")
    assert len(trace.events) == 1


def test_trace_collector_collect_observation():
    """Test that observation event can be collected."""
    from graph.trace_collector import TraceCollector
    
    collector = TraceCollector()
    collector.start_trace("test_task")
    
    collector.collect_observation(
        task_id="test_task",
        observation_data={"is_recovery_observation": False}
    )
    
    trace = collector.get_trace("test_task")
    assert len(trace.events) == 1


def test_trace_collector_collect_verification():
    """Test that verification event can be collected."""
    from graph.trace_collector import TraceCollector
    
    collector = TraceCollector()
    collector.start_trace("test_task")
    
    collector.collect_verification(
        task_id="test_task",
        verification_data={"status": "VERIFIED"}
    )
    
    trace = collector.get_trace("test_task")
    assert len(trace.events) == 1


def test_trace_collector_collect_recovery():
    """Test that recovery event can be collected."""
    from graph.trace_collector import TraceCollector
    
    collector = TraceCollector()
    collector.start_trace("test_task")
    
    collector.collect_recovery(
        task_id="test_task",
        recovery_data={"recovery_strategy": "retry"}
    )
    
    trace = collector.get_trace("test_task")
    assert len(trace.events) == 1


def test_trace_collector_collect_reflection():
    """Test that reflection event can be collected."""
    from graph.trace_collector import TraceCollector
    
    collector = TraceCollector()
    collector.start_trace("test_task")
    
    collector.collect_reflection(
        task_id="test_task",
        reflection_data={"learned": "something"}
    )
    
    trace = collector.get_trace("test_task")
    assert len(trace.events) == 1


def test_trace_collector_collect_final_outcome():
    """Test that final outcome event can be collected."""
    from graph.trace_collector import TraceCollector
    
    collector = TraceCollector()
    collector.start_trace("test_task")
    
    collector.collect_final_outcome(
        task_id="test_task",
        outcome_data={"success": True}
    )
    
    trace = collector.get_trace("test_task")
    assert len(trace.events) == 1


# ─── NODE INTEGRATION TESTS ───────────────────────────────────────────────────

def test_intake_node_collects_trace():
    """Test that intake node collects trace event."""
    from graph.nodes.intake import intake_node
    from migration.graph_state import OperonixState
    from migration.domain_contracts import TaskRequest, TaskSource
    from graph.trace_collector import get_trace_collector
    
    # Override global service
    import graph.trace_collector as trace_collector_module
    original_collector = trace_collector_module._trace_collector
    trace_collector_module._trace_collector = trace_collector_module.TraceCollector()
    
    try:
        task = TaskRequest(user_input="Open Firefox", source=TaskSource.VOICE)
        state = OperonixState(task=task)
        
        result = intake_node(state)
        
        trace_collector = get_trace_collector()
        trace = trace_collector.get_trace(task.task_id)
        
        assert trace is not None
        assert len(trace.events) == 1
        assert trace.events[0].event_type.value == "request"
    finally:
        trace_collector_module._trace_collector = original_collector


def test_analyze_intent_node_collects_trace():
    """Test that analyze_intent node collects trace event."""
    from graph.nodes.analyze_intent import analyze_intent_node
    from migration.graph_state import OperonixState
    from migration.domain_contracts import TaskRequest, TaskSource
    from graph.trace_collector import get_trace_collector
    
    # Override global service
    import graph.trace_collector as trace_collector_module
    original_collector = trace_collector_module._trace_collector
    trace_collector_module._trace_collector = trace_collector_module.TraceCollector()
    
    try:
        task = TaskRequest(user_input="Open Firefox", source=TaskSource.VOICE)
        state = OperonixState(task=task)
        
        result = analyze_intent_node(state)
        
        trace_collector = get_trace_collector()
        trace = trace_collector.get_trace(task.task_id)
        
        assert trace is not None
        assert len(trace.events) == 1
        assert trace.events[0].event_type.value == "intent"
    finally:
        trace_collector_module._trace_collector = original_collector


def test_create_plan_node_collects_trace():
    """Test that create_plan node collects trace event."""
    from graph.nodes.create_plan import create_plan_node
    from migration.graph_state import OperonixState
    from migration.domain_contracts import TaskRequest, TaskSource
    from graph.trace_collector import get_trace_collector
    
    # Override global service
    import graph.trace_collector as trace_collector_module
    original_collector = trace_collector_module._trace_collector
    trace_collector_module._trace_collector = trace_collector_module.TraceCollector()
    
    try:
        task = TaskRequest(user_input="Open Firefox", source=TaskSource.VOICE)
        state = OperonixState(task=task)
        
        result = create_plan_node(state)
        
        trace_collector = get_trace_collector()
        trace = trace_collector.get_trace(task.task_id)
        
        assert trace is not None
        assert len(trace.events) == 1
        assert trace.events[0].event_type.value == "plan"
    finally:
        trace_collector_module._trace_collector = original_collector
