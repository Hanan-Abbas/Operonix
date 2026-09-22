# State Migration Strategy — Active Tasks to Graph State

**Date:** 2026-09-22  
**Purpose:** Strategy for migrating from legacy `active_tasks` dict to LangGraph `OperonixState`

---

## Current State of Affairs

### Legacy State Management
```python
# core/orchestrator.py
self.active_tasks[task_id] = {
    "status": "gathering_context",
    "input": user_text,
    "source": event.data.get("source", "unknown"),
    "preferred_method": event.data.get("preferred_method"),
    "profile_hint": event.data.get("profile_hint"),
    "cwd": event.data.get("cwd"),
    "context": {},
    "started_at": time.monotonic(),
}
```

### New Graph State Management
```python
# migration/graph_state.py
class OperonixState(BaseModel):
    task: TaskRequest           # task_id, user_input, source, created_at
    intent: Optional[IntentResult]      # name, confidence, parameters
    context: Optional[ContextSnapshot]  # window, app, cwd, ui_state
    plan: Optional[Plan]                # steps, current_step, completed_steps
    routing: Optional[MethodDecision]   # selected method, candidates
    safety: Optional[SafetyDecision]     # risk_level, confirmation_required
    execution: Optional[ExecutionResult] # success, result, method_used
    verification: Optional[VerificationResult]  # observed vs expected
    recovery: Optional[RecoveryDecision]      # failure handling
    reflection: Optional[ReflectionResult]    # learning feedback
    final: Optional[FinalResult]              # terminal result
```

---

## Migration Strategy

### Phase 1: Coexistence (Current State)
- **Goal:** Both systems run in parallel
- **Implementation:** Feature flag controls which system handles new tasks
- **Risk:** Low - either system can handle all tasks
- **Duration:** Until graph is proven stable in production

**Current Implementation:**
```python
# USE_LANGGRAPH=true → Graph handles tasks
# USE_LANGGRAPH=false → Legacy orchestrator handles tasks
```

### Phase 2: Shadow Mode
- **Goal:** Both systems process same tasks for comparison
- **Implementation:** Duplicate events to both systems, compare results
- **Risk:** Medium - requires dual execution and result comparison
- **Duration:** 1-2 weeks of production testing

**Implementation Plan:**
```python
# core/lifecycle_manager.py
def _setup_shadow_mode(self):
    """Set up shadow mode for comparison testing."""
    if not flags.MIGRATION_SHADOW_MODE:
        return
    
    def handle_shadow_task(event):
        # Send to both systems
        asyncio.create_task(self._execute_graph_task_shadow(event))
        # Let legacy handle normally
        
    bus.subscribe("user_input_received", handle_shadow_task, priority=5)
```

### Phase 3: Gradual Migration
- **Goal:** Migrate specific task types to graph while keeping others on legacy
- **Implementation:** Task-type based routing (simple → graph, complex → legacy initially)
- **Risk:** Medium - requires task classification
- **Duration:** 2-4 weeks

**Implementation Plan:**
```python
# core/lifecycle_manager.py
def _should_use_graph_for_task(self, user_input: str) -> bool:
    """Determine if a task should use graph based on complexity."""
    # Simple tasks: file operations, app launches → graph
    # Complex tasks: multi-step, unknown operations → legacy initially
    
    simple_patterns = [
        r"open (firefox|chrome|terminal)",
        r"create (file|directory)",
        r"list (files|directory)",
        r"delete file"
    ]
    
    import re
    for pattern in simple_patterns:
        if re.search(pattern, user_input, re.IGNORECASE):
            return True
    
    return False  # Default to legacy for complex tasks
```

### Phase 4: Full Migration
- **Goal:** All tasks go through graph, legacy becomes fallback only
- **Implementation:** Set USE_LANGGRAPH=true, keep legacy as emergency fallback
- **Risk:** Low - fallback path available
- **Duration:** 1-2 weeks monitoring

### Phase 5: Legacy Retirement
- **Goal:** Remove legacy orchestrator and active_tasks dict
- **Implementation:** Delete legacy code after graph is proven stable
- **Risk:** High - irreversible, requires extensive testing
- **Duration:** Only after 4+ weeks of stable graph operation

---

## State Mapping

### Legacy → Graph State Mapping

| Legacy Field | Graph State Field | Transformation |
|-------------|------------------|----------------|
| `task_id` | `task.task_id` | Direct mapping |
| `input` | `task.user_input` | Direct mapping |
| `source` | `task.source` | String → TaskSource enum |
| `profile_hint` | `intent.profile_hint` | Move to intent result |
| `cwd` | `context.cwd` | Move to context snapshot |
| `context` | `context` | Expand to ContextSnapshot |
| `preferred_method` | `routing.selected_candidate` | Move to routing decision |
| `status` | Multiple fields | Distributed across execution, final |

### State Translation Function

```python
# migration/state_translator.py
def translate_legacy_to_graph_state(legacy_task: dict) -> OperonixState:
    """Translate legacy active_tasks dict to OperonixState.
    
    Args:
        legacy_task: Legacy task dict from orchestrator.active_tasks
        
    Returns:
        OperonixState for graph execution
    """
    from migration.domain_contracts import (
        TaskRequest, TaskSource, ContextSnapshot, OperonixState
    )
    
    # Map source string to enum
    source_map = {
        "voice": TaskSource.VOICE,
        "panel": TaskSource.PANEL,
        "api": TaskSource.API,
        "cli": TaskSource.CLI,
        "unknown": TaskSource.API
    }
    
    # Create task request
    task_request = TaskRequest(
        task_id=legacy_task.get("task_id", str(uuid.uuid4())),
        user_input=legacy_task.get("input", ""),
        source=source_map.get(legacy_task.get("source", "unknown"), TaskSource.API),
        metadata={
            "legacy_task": True,
            "preferred_method": legacy_task.get("preferred_method"),
            "profile_hint": legacy_task.get("profile_hint")
        }
    )
    
    # Create context snapshot
    legacy_context = legacy_task.get("context", {})
    context_snapshot = ContextSnapshot(
        active_window=legacy_context.get("window_title"),
        app=legacy_context.get("app_name"),
        app_type=legacy_context.get("app_type"),
        window_title=legacy_context.get("window_title"),
        cwd=legacy_context.get("cwd"),
        sub_context=legacy_context.get("sub_context"),
        ui_state=legacy_context.get("ui_state", {}),
        permissions=legacy_context.get("permissions", []),
        confidence=legacy_context.get("confidence", 0.0)
    )
    
    # Create OperonixState
    return OperonixState(
        task=task_request,
        context=context_snapshot
    )
```

---

## Dual State Management

### Transition Period Strategy

During Phases 1-3, both state systems will coexist:

```python
# core/orchestrator.py
class Orchestrator:
    def __init__(self):
        self.active_tasks: dict[str, dict[str, Any]] = {}  # Legacy
        self.graph_states: dict[str, OperonixState] = {}   # New
    
    async def handle_new_task(self, event: Any) -> None:
        task_id = str(uuid.uuid4())[:8]
        
        # Determine which system should handle this task
        use_graph = self._should_use_graph_for_task(event.data.get("text", ""))
        
        if use_graph:
            # Route to graph
            await self._route_to_graph(task_id, event)
        else:
            # Use legacy path
            self.active_tasks[task_id] = { /* legacy state */ }
            # ... existing legacy logic
```

### State Synchronization

For shadow mode and comparison:

```python
# migration/state_synchronizer.py
class StateSynchronizer:
    """Synchronize state between legacy and graph systems for comparison."""
    
    def compare_states(self, legacy_task: dict, graph_state: OperonixState) -> dict:
        """Compare legacy and graph states for validation.
        
        Returns:
            Dict with comparison results and any discrepancies
        """
        comparison = {
            "task_id": legacy_task.get("task_id"),
            "status_match": self._compare_status(legacy_task, graph_state),
            "context_match": self._compare_context(legacy_task, graph_state),
            "execution_match": self._compare_execution(legacy_task, graph_state),
            "discrepancies": []
        }
        
        # Collect discrepancies
        if not comparison["status_match"]:
            comparison["discrepancies"].append("status_mismatch")
        
        return comparison
```

---

## Data Migration

### Existing Active Tasks Migration

For running tasks during migration:

```python
# migration/task_migrator.py
class TaskMigrator:
    """Migrate running tasks from legacy to graph state."""
    
    async def migrate_running_tasks(self) -> dict:
        """Migrate all currently running tasks to graph state.
        
        Returns:
            Dict with migration results
        """
        from core.orchestrator import orchestrator
        
        results = {
            "migrated": 0,
            "failed": 0,
            "skipped": 0,
            "errors": []
        }
        
        for task_id, legacy_task in orchestrator.active_tasks.items():
            try:
                # Only migrate tasks in safe states
                status = legacy_task.get("status")
                if status in ["gathering_context", "intent_parsing", "planning"]:
                    # Safe to migrate - task hasn't started execution yet
                    graph_state = translate_legacy_to_graph_state(legacy_task)
                    # Store in graph state manager
                    results["migrated"] += 1
                else:
                    # Skip tasks already in execution
                    results["skipped"] += 1
                    
            except Exception as e:
                results["failed"] += 1
                results["errors"].append({
                    "task_id": task_id,
                    "error": str(e)
                })
        
        return results
```

---

## Monitoring and Validation

### State Health Checks

```python
# migration/state_health_monitor.py
class StateHealthMonitor:
    """Monitor health of state migration and dual-state operation."""
    
    def check_state_consistency(self) -> dict:
        """Check consistency between legacy and graph states.
        
        Returns:
            Dict with health check results
        """
        from core.orchestrator import orchestrator
        
        health = {
            "legacy_tasks_count": len(orchestrator.active_tasks),
            "graph_tasks_count": len(self._get_graph_states()),
            "orphaned_legacy_tasks": [],
            "orphaned_graph_tasks": [],
            "state_divergence": []
        }
        
        # Check for orphaned tasks
        legacy_ids = set(orchestrator.active_tasks.keys())
        graph_ids = set(self._get_graph_states().keys())
        
        health["orphaned_legacy_tasks"] = list(legacy_ids - graph_ids)
        health["orphaned_graph_tasks"] = list(graph_ids - legacy_ids)
        
        return health
    
    def _get_graph_states(self) -> dict:
        """Get current graph states from state manager."""
        # Implementation depends on graph state storage
        return {}
```

---

## Rollback Strategy

### Emergency Rollback Procedure

If graph state causes issues:

1. **Immediate Rollback:**
   ```bash
   # Set environment variable
   export USE_LANGGRAPH=false
   
   # Restart system
   systemctl restart operonix
   ```

2. **State Recovery:**
   ```python
   # Reconstruct legacy state from graph states
   def rollback_to_legacy_state(graph_state: OperonixState) -> dict:
       """Reconstruct legacy active_tasks dict from graph state."""
       return {
           "task_id": graph_state.task.task_id,
           "input": graph_state.task.user_input,
           "source": graph_state.task.source.value,
           "status": self._map_graph_status_to_legacy(graph_state),
           "context": self._extract_context_dict(graph_state.context),
           # ... other fields
       }
   ```

3. **Data Consistency:**
   - Run state consistency checks
   - Reconcile any divergent states
   - Validate task execution results

---

## Success Criteria

### Phase Completion Criteria

**Phase 1 (Coexistence):**
- ✅ Feature flag controls task routing
- ✅ Both systems operational independently
- ✅ No state corruption or interference
- ✅ Monitoring shows clean separation

**Phase 2 (Shadow Mode):**
- ✅ Shadow mode captures both system results
- ✅ Comparison shows graph results are equivalent or better
- ✅ Performance impact is acceptable (<10% overhead)
- ✅ No user-visible disruption

**Phase 3 (Gradual Migration):**
- ✅ Simple tasks successfully migrated to graph
- ✅ Complex tasks remain on legacy without issues
- ✅ Task classification accuracy >95%
- ✅ Error rates comparable to legacy

**Phase 4 (Full Migration):**
- ✅ All tasks go through graph
- ✅ Legacy fallback rarely triggered (<1% of tasks)
- ✅ Performance matches or exceeds legacy
- ✅ User satisfaction maintained

**Phase 5 (Legacy Retirement):**
- ✅ Graph stable for 4+ weeks
- ✅ No critical issues in production
- ✅ Complete test coverage for graph paths
- ✅ Team confident in removal

---

## Implementation Timeline

### Week 1-2: Phase 1 (Coexistence)
- Implement feature flag routing
- Add monitoring for both systems
- Test in development environment

### Week 3-4: Phase 2 (Shadow Mode)
- Implement shadow mode
- Run comparison tests
- Analyze results and fix discrepancies

### Week 5-8: Phase 3 (Gradual Migration)
- Implement task classification
- Migrate simple task types
- Monitor and adjust

### Week 9-10: Phase 4 (Full Migration)
- Enable graph for all tasks
- Keep legacy as fallback
- Monitor closely

### Week 11+: Phase 5 (Legacy Retirement)
- Monitor graph stability
- Plan legacy code removal
- Execute retirement when ready

---

## Risk Mitigation

### Primary Risks

1. **State Divergence:**
   - **Mitigation:** Comprehensive state comparison monitoring
   - **Fallback:** Immediate rollback to legacy

2. **Performance Degradation:**
   - **Mitigation:** Performance benchmarking at each phase
   - **Fallback:** Optimize graph nodes before full migration

3. **Data Loss:**
   - **Mitigation:** State backup before migration
   - **Fallback:** State reconstruction procedures

4. **User Disruption:**
   - **Mitigation:** Gradual migration with extensive testing
   - **Fallback:** Legacy system always available

### Contingency Plans

1. **Graph Unstable:**
   - Roll back to Phase 1 (coexistence)
   - Increase testing coverage
   - Fix identified issues

2. **State Corruption:**
   - Emergency rollback to legacy
   - State reconstruction from backups
   - Data consistency validation

3. **Performance Issues:**
   - Optimize critical graph nodes
   - Add caching where appropriate
   - Consider hardware upgrades if needed

---

## Conclusion

This state migration strategy provides a safe, gradual path from legacy `active_tasks` dict to LangGraph `OperonixState`. The phased approach minimizes risk while allowing continuous production operation. Each phase has clear success criteria and rollback procedures, ensuring the migration can be halted or reversed if issues arise.

The key principle is **coexistence before replacement** - both systems will run in parallel during the transition, with feature flags controlling which handles new tasks. This ensures zero downtime and provides a safety net throughout the migration process.
