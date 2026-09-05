"""
Graph Package — Operonix LangGraph Migration
────────────────────────────────────────────

This package contains the LangGraph-based workflow implementation.
Per migration plan §4, the graph owns:
- Task workflow state (OperonixState)
- Node implementations
- Routing logic
- Recovery logic
- Reflection logic

The graph does NOT own:
- EventBus (system-wide communication)
- Executor (execution engine)
- Service instances (context, safety, memory, etc.)

State holds service RESULTS, never service objects.
"""
