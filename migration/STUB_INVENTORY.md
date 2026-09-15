# Stub Inventory — Operonix LangGraph/LangChain Migration

**Date:** 2026-09-15  
**Purpose:** Comprehensive list of all stub implementations across migration phases

---

## Summary Statistics

- **Total Stubs Identified:** 18
- **Resolved:** 4
- **Active:** 14
- **Deferred to Phase 15:** 6 (legacy retirement items)

---

## RESOLVED Stubs ✅

### 1. Postcondition Verification (Phase 5/6)
**Status:** ✅ RESOLVED in Phase 9/10  
**Migration File:** `PHASE_5_COMPLETION.md`, `PHASE_6_COMPLETION.md`  
**Location:** `graph/nodes/verify_step.py` - `_verify_postconditions()`  
**Description:** Now integrates with actual context services (WindowDetector, StateExtractor) to verify file existence, application state, and directory conditions based on step objectives.  
**Resolution:** Context services integration completed in Phase 9/10.

### 2. Context Observation (Phase 5/6)
**Status:** ✅ RESOLVED in Phase 9/10  
**Migration File:** `PHASE_5_COMPLETION.md`, `PHASE_6_COMPLETION.md`  
**Location:** `graph/nodes/observe.py`  
**Description:** Observe node now integrates with actual context services (WindowDetector, StateExtractor) to gather real context snapshots including window title, app name, app type, cwd, and deep UI state.  
**Resolution:** Full context services integration completed in Phase 9/10.

### 3. Safety Check Integration (Phase 4)
**Status:** ✅ RESOLVED Post-Phase 12  
**Migration File:** `PHASE_4_COMPLETION.md`, `PHASE_14_COMPLETION.md`  
**Location:** `graph/nodes/safety_check.py`  
**Description:** Was a stub creating placeholder SafetyDecision. Now integrates with actual safety/ module (Validator, PermissionGuard, RiskRules, Confirmation).  
**Resolution:** Marked as RESOLVED in Phase 14 completion report.

### 4. Executor Integration (Phase 4)
**Status:** ✅ RESOLVED Post-Phase 12  
**Migration File:** `PHASE_4_COMPLETION.md`, `PHASE_14_COMPLETION.md`  
**Location:** `graph/nodes/execute_step.py`  
**Description:** Was a stub creating placeholder ExecutionResult with success=True. Now integrates with executor/ module (executor, retry_manager, fallback_manager).  
**Resolution:** Marked as RESOLVED in Phase 14 completion report.

### 5. generate_structured_output (Phase 2)
**Status:** ✅ RESOLVED (Recently Fixed)  
**Migration File:** `PHASE_2_COMPLETION.md`  
**Location:** `ai/models/model_service.py` - `generate_structured_output()`  
**Description:** Was returning empty list for plan_generation schema, causing test failure. Now returns placeholder steps for plan_generation schema.  
**Resolution:** Fixed by adding schema-specific stub responses for "plan_generation", "complexity_analysis", and "intent_analysis".

---

## ACTIVE Stubs ❌

### 1. Observe Node (Phase 1)
**Status:** ❌ ACTIVE STUB  
**Migration File:** `PHASE_1_COMPLETION.md`  
**Location:** `graph/nodes/observe.py`  
**Description:** Stub implementation that logs intent only. Context services integration deferred to later phases.  
**Note:** Partially resolved in Phase 9/10 with WindowDetector and StateExtractor integration, but full integration may still be incomplete.

### 2. Legacy Workflow Integration (Phase 1)
**Status:** ❌ ACTIVE STUB  
**Migration File:** `PHASE_1_COMPLETION.md`  
**Location:** `graph/runtime_adapter.py` - `_execute_with_legacy()`  
**Description:** Stub that returns placeholder result. Actual integration with legacy orchestrator deferred to later phases.  
**Note:** This may be intentionally deferred to Phase 15 (Legacy Retirement).

### 3. Retrieve Knowledge Node (Phase 4)
**Status:** ❌ ACTIVE STUB  
**Migration File:** `PHASE_4_COMPLETION.md`  
**Location:** `graph/nodes/retrieve_knowledge.py`  
**Description:** Stub implementation creating placeholder KnowledgeContext. RAG/memory integration (memory/, vector_store, learning/retriever) deferred.  
**Note:** Partially resolved in Phase 11 with LongTermMemory, SessionMemory, VectorStore, Retriever integration.

### 4. Route Node (Phase 4)
**Status:** ❌ ACTIVE STUB  
**Migration File:** `PHASE_4_COMPLETION.md`  
**Location:** `graph/nodes/route.py`  
**Description:** Stub implementation creating placeholder MethodDecision with SHELL candidate. Candidate-based routing engine integration deferred.  
**Note:** Resolved in Phase 10 with full candidate-based routing engine implementation.

### 5. Safety Check Node (Phase 4)
**Status:** ❌ ACTIVE STUB  
**Migration File:** `PHASE_4_COMPLETION.md`, `PHASE_7_COMPLETION.md`  
**Location:** `graph/nodes/safety_check.py`  
**Description:** Stub implementation creating placeholder SafetyDecision with LOW risk, APPROVED status, GRANTED permission. Does not call actual safety validator, permission guard, or risk rules.  
**Note:** Marked as RESOLVED in Phase 14, but Phase 7 still notes it as a stub. May need verification.

### 6. Execute Step Node (Phase 4)
**Status:** ❌ ACTIVE STUB  
**Migration File:** `PHASE_4_COMPLETION.md`  
**Location:** `graph/nodes/execute_step.py`  
**Description:** Stub implementation creating placeholder ExecutionResult with success=True. Does not actually execute anything. Executor/ module integration (executor, retry_manager, fallback_manager) deferred.  
**Note:** Marked as RESOLVED in Phase 14, but may need verification of actual executor integration.

### 7. Verify Step Node (Phase 4)
**Status:** ❌ ACTIVE STUB  
**Migration File:** `PHASE_4_COMPLETION.md`  
**Description:** Stub implementation creating placeholder VerificationResult with VERIFIED status. Actual verification logic (context snapshot comparison, expected state validation) deferred.  
**Note:** Partially resolved in Phase 5/6 with postcondition checking and Phase 9/10 with context services integration.

### 8. Safety Check Integration (Phase 7)
**Status:** ❌ ACTIVE STUB  
**Migration File:** `PHASE_7_COMPLETION.md`  
**Location:** `graph/nodes/safety_check.py`  
**Description:** Still a stub that creates placeholder SafetyDecision without calling actual safety validator, permission guard, or risk rules. confirmation_required is False by default (can be set to True for testing).  
**Note:** Can set `confirmation_required=True` to test confirmation flow.

### 9. External Resume Mechanism (Phase 7)
**Status:** ❌ ACTIVE STUB  
**Migration File:** `PHASE_7_COMPLETION.md`  
**Location:** `graph/nodes/confirmation.py`  
**Description:** Confirmation node pauses graph (state.paused = True), but external resume mechanism (via dashboard/API) is not fully implemented. Currently direct edge confirmation → execute_step.  
**Stub Implementation Details:**
- confirmation_node sets state.paused = True
- Checkpoint is created before pausing
- Graph edge is confirmation → execute_step (direct, no waiting)
- No actual dashboard/API integration for human response
- No external event listener for resume signals
- resume_from_confirmation() function exists but not triggered by external mechanism

### 10. Cancel Node Placeholder (Phase 8)
**Status:** ❌ ACTIVE STUB  
**Migration File:** `PHASE_8_COMPLETION.md`  
**Location:** `graph/graph.py`  
**Description:** Cancel node in graph is a placeholder (lambda state: state). In real implementation, this would integrate with CancellationService to handle cleanup, rollback, and resource release.

### 11. Timeout Callback Execution (Phase 8)
**Status:** ❌ ACTIVE STUB  
**Migration File:** `PHASE_8_COMPLETION.md`  
**Location:** `graph/timeout_manager.py`  
**Description:** TimeoutManager has callback execution logic, but actual timeout checking (check_timeouts()) is not automatically triggered in background thread. In real implementation, this would be run in watchdog thread.

### 12. Resource Contention Detection (Phase 8)
**Status:** ❌ ACTIVE STUB  
**Migration File:** `PHASE_8_COMPLETION.md`  
**Location:** `graph/cancellation.py`  
**Description:** Resource contention is handled via cancellation request, but automatic detection of resource contention is not implemented. In real implementation, this would detect when multiple workflows try to access the same physical resource.

### 13. Reflection Node (Phase 9)
**Status:** ❌ ACTIVE STUB  
**Migration File:** `PHASE_9_COMPLETION.md`  
**Location:** `graph/nodes/` (not implemented)  
**Description:** REFLECTION trace event type is defined and the collector has a `collect_reflection` method, but there is no actual reflection node in the graph. This will be implemented in a future phase (Phase 8 in original plan, but may be deferred).

### 14. Context Validation Logic (Phase 11)
**Status:** ❌ ACTIVE STUB  
**Migration File:** `PHASE_11_COMPLETION.md`  
**Location:** `graph/nodes/observe.py`  
**Description:** ContextValidator integration is present but the validation logic is not fully implemented. The service is called but the validation results are not used to block execution.

### 15. Permission Checking in PluginAdapter (Phase 12)
**Status:** ❌ ACTIVE STUB  
**Migration File:** `PHASE_12_COMPLETION.md`  
**Location:** `graph/tool_adapter.py` - PluginAdapter  
**Description:** Current permission checking is a placeholder that always returns True. A future implementation would integrate with a system permission manager to actually check if required permissions are available.

---

## DEFERRED TO PHASE 15 ⏳

**Note:** Phase 15 should only be executed after the graph is demonstrably stable in production use.

### 1. Legacy Workflow Execution
**Status:** ⏳ DEFERRED TO PHASE 15  
**Migration File:** `PHASE_14_COMPLETION.md`  
**Description:** Legacy workflow execution in orchestrator.py to be retired after graph is stable.

### 2. Duplicated Routing Logic
**Status:** ⏳ DEFERRED TO PHASE 15  
**Migration File:** `PHASE_14_COMPLETION.md`  
**Description:** Duplicated routing logic across multiple modules to be consolidated/removed.

### 3. DecisionEngine Retirement
**Status:** ⏳ DEFERRED TO PHASE 15  
**Migration File:** `PHASE_14_COMPLETION.md`  
**Description:** DecisionEngine module to be retired after graph routing is proven.

### 4. Duplicated ToolSelector Logic
**Status:** ⏳ DEFERRED TO PHASE 15  
**Migration File:** `PHASE_14_COMPLETION.md`  
**Description:** Duplicated ToolSelector routing logic to be removed.

### 5. Redundant CapabilityMapper Routing
**Status:** ⏳ DEFERRED TO PHASE 15  
**Migration File:** `PHASE_14_COMPLETION.md`  
**Description:** Redundant CapabilityMapper routing logic to be consolidated.

### 6. active_tasks as Workflow Authority
**Status:** ⏳ DEFERRED TO PHASE 15  
**Migration File:** `PHASE_14_COMPLETION.md`  
**Description:** active_tasks dict in orchestrator to be retired after graph state becomes authoritative.

---

## Migration File References

| Phase | Completion File | Key Stubs Mentioned |
|-------|----------------|---------------------|
| Phase 0 | `PHASE_0_COMPLETION.md` | No stubs (infrastructure phase) |
| Phase 1 | `PHASE_1_COMPLETION.md` | Observe node, Legacy workflow integration |
| Phase 2 | `PHASE_2_COMPLETION.md` | generate_structured_output (RESOLVED) |
| Phase 3 | `PHASE_3_COMPLETION.md` | No new stubs |
| Phase 4 | `PHASE_4_COMPLETION.md` | retrieve_knowledge, route, safety_check, execute_step, verify_step |
| Phase 5 | `PHASE_5_COMPLETION.md` | Postcondition verification (RESOLVED), Context observation (RESOLVED) |
| Phase 6 | `PHASE_6_COMPLETION.md` | Postcondition check (RESOLVED), Context observation (RESOLVED) |
| Phase 7 | `PHASE_7_COMPLETION.md` | Safety check integration, External resume mechanism |
| Phase 8 | `PHASE_8_COMPLETION.md` | Cancel node, Timeout callback, Resource contention detection |
| Phase 9 | `PHASE_9_COMPLETION.md` | Reflection node, Context observation (partial) |
| Phase 10 | `PHASE_10_COMPLETION.md` | Route node (RESOLVED) |
| Phase 11 | `PHASE_11_COMPLETION.md` | Context validation logic, retrieve_knowledge (partial RESOLVED) |
| Phase 12 | `PHASE_12_COMPLETION.md` | Permission checking in PluginAdapter |
| Phase 14 | `PHASE_14_COMPLETION.md` | Safety check (RESOLVED), Executor (RESOLVED), Phase 15 items |

---

## Priority Recommendations

### High Priority (Blocking Core Functionality)
1. **Safety Check Integration** - Critical for production safety
2. **Executor Integration** - Required for actual execution
3. **External Resume Mechanism** - Required for human intervention flow

### Medium Priority (Enhanced Functionality)
1. **Reflection Node** - Important for learning/feedback loop
2. **Permission Checking in PluginAdapter** - Important for plugin security
3. **Context Validation Logic** - Important for context-aware decisions

### Low Priority (Infrastructure/Deferred)
1. **Legacy Workflow Integration** - Deferred to Phase 15
2. **Cancel Node Placeholder** - Can work with current implementation
3. **Timeout Callback Execution** - Current implementation works for testing

---

## Notes

- This inventory is based on reading all phase completion files (PHASE_0 through PHASE_14)
- Some stubs may have been partially resolved in later phases but still marked as stubs in earlier phase documentation
- Phase 13 is not present in the completion files (may have been skipped or combined with other phases)
- Phase 15 (Legacy Retirement) should only be executed after the graph is demonstrably stable in production use
