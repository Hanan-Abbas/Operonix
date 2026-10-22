# Phase 3: Linear Routing Analysis

## Overview
This document identifies all locations where linear routing/fallback chains are currently implemented
before implementing candidate-based routing per migration plan §4.3.

---

## 1. **tools/method_router.py** - LEGACY LINEAR ROUTING (Primary Source)

### Location: Lines 10-16, 150-233

**Current Linear Priority:**
```python
Routing priority (enforced in MethodRouter.select())
─────────────────────────────────────────────────────
  1. PLUGIN  — a registered, trusted plugin scores >= PLUGIN_CONFIDENCE_THRESHOLD
  2. API     — intent maps to a known APITool endpoint (O(1) lookup, no probe)
  3. SHELL   — no plugin/API match; risk score permits shell execution
  4. UI      — all upper methods return zero confidence; static compatibility check
```

**Implementation in `select()` method:**
- Lines 150-165: Layer 1 - PLUGIN evaluation → if passes, return with fallback `(API, SHELL, UI)`
- Lines 171-190: Layer 2 - API evaluation → if passes, return with fallback `(SHELL, UI)`
- Lines 192-211: Layer 3 - SHELL evaluation → if passes, return with fallback `(UI)`
- Lines 213-233: Layer 4 - UI evaluation → if passes, return with fallback `()`

**Fallback Chain Generation:**
- Line 330-334: PLUGIN fallback = `(API, SHELL, UI)`
- Line 382-383: API fallback = `(SHELL, UI)`
- Line 459: SHELL fallback = `(UI)`
- Line 522: UI fallback = `()` (last resort)

**Impact:** This is the MAIN linear routing implementation that needs to be replaced by candidate-based routing.

---

## 2. **executor/executor.py** - LINEAR FALLBACK IN EXECUTION

### Location: Lines 548-755

**Current Behavior:**
- `_execute_with_decision()` walks the `fallback_chain` tuple
- Line 571: `current = decision`
- Line 576-632: Loop continues through fallback_chain
- Line 754-755: Advances to next fallback on `ROUTING_MISMATCH` or `ENV_PERMANENT`

**Fallback Logic:**
```python
while True:
    method = current.method
    # ... execute ...
    # On failure, call _handle_failure which advances fallback
```

**Impact:** Executor currently honors linear fallback_chain from legacy MethodRouter. In Phase 2, we set fallback_chain to empty tuple in execute_step.py, but executor still has the logic to walk it.

---

## 3. **tools/routing_decision.py** - DATA STRUCTURE FOR LINEAR FALLBACK

### Location: Lines 16, 176, 209, 227-229, 264, 267, 275-276, 279-280, 293

**MethodDecision Data Structure:**
```python
@dataclass(frozen=True)
class MethodDecision:
    method: MethodType
    confidence: float
    fallback_chain: tuple[MethodType, ...]  # <-- LINEAR FALLBACK CHAIN
    payload: LayeredPayload
    expected_app: str | None
    expected_ui_state: MappingProxyType | None
    rejected: tuple[tuple[MethodType, str], ...]
```

**Impact:** This data structure is designed for linear fallback. After candidate-based routing, the fallback_chain should be empty or removed entirely.

---

## 4. **tools/tool_selector.py** - LINEAR TOOL SELECTION

### Location: Lines 117-165

**Current Logic:**
- Tries tools in priority order
- Falls back to next tool if current fails

**Impact:** This is a secondary linear selection that should be replaced by candidate-based routing.

---

## 5. **executor/fallback_manager.py** - FALLBACK MANAGEMENT

### Location: Lines 11-70

**Current Behavior:**
- Manages fallback chains for execution
- Tracks which methods have been tried
- Returns next fallback method

**Impact:** This module manages linear fallback state. After candidate-based routing, this should be simplified or removed.

---

## 6. **graph/nodes/route.py** - GRAPH ROUTING NODE (Already Uses Candidate-Based)

### Location: Lines 1-231

**Current Status:**
- Lines 73-100: Already integrates candidate-based routing services
- Lines 205-231: Has fallback to SHELL if candidate-based routing fails

**Impact:** The graph route node is already partially candidate-based but still has a linear fallback to SHELL.

---

## 7. **graph/nodes/execute_step.py** - ALREADY FIXED IN PHASE 2

### Location: Lines 317, 347

**Current Status:**
- Line 317: `fallback_chain = tuple()` - **Already empty**
- Line 347: Passes empty fallback_chain to executor

**Impact:** Phase 2 already removed routing fallback from the graph→executor bridge.

---

## 8. **Legacy Orchestrator Paths** - NOT USED IN LANGGRAPH MODE

### Locations:
- `core/orchestrator.py` - Orchestrator no longer subscribes to task events in LangGraph mode (Phase 2)
- `brain/decision_engine.py` - Legacy decision engine
- `brain/intent_parser.py` - Legacy intent parsing

**Impact:** These are legacy paths and should not be used when `USE_LANGGRAPH=true`.

---

## Summary of Changes Required for Phase 3

### Primary Changes (Must Do):

1. **tools/method_router.py**
   - Add conditional: only use linear routing when `USE_GRAPH_ROUTING=false`
   - When `USE_GRAPH_ROUTING=true`, disable linear fallback generation
   - Keep for legacy mode compatibility

2. **graph/nodes/route.py**
   - Remove linear fallback to SHELL (line 205-231)
   - When candidate-based routing fails, trigger recovery instead of fallback
   - Add safety constraint filtering before ranking (per migration plan §4.4)

3. **tools/routing_decision.py**
   - Keep fallback_chain field for backward compatibility
   - Document that it should be empty in candidate-based routing mode

4. **executor/executor.py**
   - Add conditional: only walk fallback_chain when `USE_GRAPH_ROUTING=false`
   - When `USE_GRAPH_ROUTING=true`, executor should only retry execution-level failures
   - Routing-level failures should return to graph for re-routing

### Secondary Changes (Should Do):

5. **tools/tool_selector.py**
   - Add conditional to disable linear selection in graph mode
   - Keep for legacy mode

6. **executor/fallback_manager.py**
   - Simplify when `USE_GRAPH_ROUTING=true`
   - Keep for legacy mode

### No Changes Needed:

7. **graph/nodes/execute_step.py** - Already fixed in Phase 2
8. **core/orchestrator.py** - Already fixed in Phase 2
9. Legacy brain modules - Not used in LangGraph mode

---

## Feature Flag Strategy

Use `USE_GRAPH_ROUTING` to control the routing strategy:

- **USE_GRAPH_ROUTING=false (Legacy Mode):**
  - Use linear routing in `tools/method_router.py`
  - Executor walks fallback_chain
  - Orchestrator uses legacy paths

- **USE_GRAPH_ROUTING=true (Graph Mode):**
  - Graph route node uses candidate-based routing
  - `tools/method_router.py` disabled or returns simple decision
  - Executor does NOT walk fallback_chain
  - Routing failures return to graph for re-routing

---

## Implementation Order

1. Add `USE_GRAPH_ROUTING` conditional to `tools/method_router.py`
2. Add `USE_GRAPH_ROUTING` conditional to `executor/executor.py`
3. Remove linear fallback from `graph/nodes/route.py`
4. Add safety constraint filtering to `graph/nodes/route.py`
5. Test both modes with feature flag toggle
6. Update documentation

---

## Risks

1. **Breaking Legacy Mode:** Must ensure `USE_GRAPH_ROUTING=false` still works
2. **Executor Behavior:** Executor's fallback logic is deeply integrated
3. **Test Coverage:** Need comprehensive tests for both modes
4. **Migration Path:** Ensure smooth transition from linear to candidate-based

---

## Next Steps

Before implementing, verify:
- [ ] All linear routing locations identified
- [ ] Feature flag strategy approved
- [ ] Test plan for both modes
- [ ] Rollback strategy if issues arise
