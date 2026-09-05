"""
Graph Nodes — Operonix LangGraph Migration
─────────────────────────────────────────

Individual node implementations for the Operonix workflow graph.
Per migration plan §4.2, nodes are thin adapters over existing services
during migration, with internals migrated gradually afterward.

Nodes should:
- Accept OperonixState as input
- Return updated OperonixState
- Call services (not own them)
- Add history events for observability
"""
