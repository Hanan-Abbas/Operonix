# Phase 3 Implementation Plan: Candidate-Based Routing

## Pre-Implementation Status

✅ **All Pre-Implementation Tests Passed**
- Test 1: Legacy Linear Routing - PASSED
- Test 2: Candidate-Based Routing Services - PASSED
- Test 3: Executor Fallback Behavior - PASSED
- Test 4: Toggle Switching - PASSED
- Test 5: Backward Compatibility - PASSED

**Risk Resolution Status:**
- ✅ Breaking Legacy Mode: LEGACY MODE TESTED AND WORKING
- ✅ Executor Behavior: EXECUTOR TESTED WITH EMPTY FALLBACK_CHAIN
- ✅ Test Coverage: COMPREHENSIVE TESTS FOR BOTH MODES
- ✅ Migration Path: TOGGLE SWITCHING VALIDATED

---

## Implementation Strategy

### Feature Flag Control

Use `USE_GRAPH_ROUTING` to control routing strategy:

**USE_GRAPH_ROUTING=false (Legacy Mode):**
- Use linear routing in `tools/method_router.py`
- Executor walks fallback_chain
- Legacy orchestrator paths active
- Graph route node falls back to SHELL

**USE_GRAPH_ROUTING=true (Graph Mode):**
- Graph route node uses candidate-based routing
- `tools/method_router.py` returns simple decision (no linear fallback)
- Executor does NOT walk fallback_chain
- Routing failures return to graph for re-routing

---

## Implementation Steps

### Step 3.1: Add Conditional to tools/method_router.py

**File:** `tools/method_router.py`

**Changes:**
- Check `USE_GRAPH_ROUTING` flag at start of `select()` method
- If `USE_GRAPH_ROUTING=true`, return simple decision without linear fallback chain
- If `USE_GRAPH_ROUTING=false`, use existing linear routing logic
- Add logging to indicate which mode is active

**Rationale:**
- Preserves legacy mode when flag is false
- Disables linear fallback when graph mode is active
- Prevents duplicate routing logic

---

### Step 3.2: Add Conditional to executor/executor.py

**File:** `executor/executor.py`

**Changes:**
- Check `USE_GRAPH_ROUTING` flag in `_execute_with_decision()` method
- If `USE_GRAPH_ROUTING=true`, skip fallback_chain walking
- If `USE_GRAPH_ROUTING=false`, use existing fallback logic
- On routing-level failure in graph mode, return error to graph

**Rationale:**
- In graph mode, executor should only retry execution-level failures
- Routing-level failures should be handled by graph's recovery node
- Preserves legacy executor behavior when flag is false

---

### Step 3.3: Remove Linear Fallback from graph/nodes/route.py

**File:** `graph/nodes/route.py`

**Changes:**
- Remove `_create_fallback_decision()` function (lines 205-231)
- When candidate-based routing fails, trigger recovery instead of fallback
- Return None or empty decision to let recovery node handle it
- Add logging to indicate routing failure triggers recovery

**Rationale:**
- Candidate-based routing should not have linear fallback
- Routing failures should go through recovery node
- Aligns with migration plan §4.3

---

### Step 3.4: Add Safety Constraint Filtering to graph/nodes/route.py

**File:** `graph/nodes/route.py`

**Changes:**
- Add safety constraint filtering before ranking (per migration plan §4.4)
- Import safety/risk_rules.py and safety/permission_guard.py
- Filter out risky candidates before ranking
- Filter out unauthorized candidates before ranking
- Only rank candidates that pass safety constraints

**Rationale:**
- Safety should filter candidates before ranking (not just before execution)
- Aligns with migration plan §4.4
- Reduces risk by eliminating unsafe candidates early

---

### Step 3.5: Update .env with USE_GRAPH_ROUTING=true

**File:** `.env`

**Changes:**
- Set `USE_GRAPH_ROUTING=true` to enable candidate-based routing
- Document the change in comments

**Rationale:**
- Enable the new routing mode after implementation
- Allow testing of candidate-based routing

---

### Step 3.6: Post-Implementation Testing

**File:** `test_phase_3_routing_modes.py` (already created)

**Changes:**
- Run the pre-implementation tests again after implementation
- Verify both modes still work
- Verify no regressions in legacy mode
- Verify candidate-based routing works correctly

---

## Rollback Strategy

If issues arise after implementation:

1. **Immediate Rollback:** Set `USE_GRAPH_ROUTING=false` in .env
2. **Code Rollback:** Revert changes to each file in reverse order
3. **Validation:** Run pre-implementation tests to verify legacy mode still works

---

## Success Criteria

After implementation:

1. ✅ `USE_GRAPH_ROUTING=false` preserves legacy linear routing
2. ✅ `USE_GRAPH_ROUTING=true` uses candidate-based routing
3. ✅ Executor respects routing mode
4. ✅ No dual routing logic
5. ✅ Safety constraints filter candidates before ranking
6. ✅ Routing failures trigger recovery, not linear fallback
7. ✅ All pre-implementation tests still pass

---

## Timeline Estimate

- Step 3.1: 30 minutes
- Step 3.2: 30 minutes
- Step 3.3: 15 minutes
- Step 3.4: 30 minutes
- Step 3.5: 5 minutes
- Step 3.6: 15 minutes

**Total: ~2 hours**

---

## Notes

- This is a major change affecting core routing logic
- Feature flag ensures backward compatibility
- Comprehensive testing validates both modes
- Rollback strategy is simple (toggle flag)
