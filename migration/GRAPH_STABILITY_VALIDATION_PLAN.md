# Graph Stability Validation Plan — Operonix LangGraph/LangChain Migration

**Date:** 2026-09-12  
**Purpose:** Validate graph stability before Phase 15 (Legacy Retirement)

---

## Overview

Before retiring the legacy async/event-driven state management code, we must validate that the LangGraph-based workflow is stable and production-ready. This plan outlines comprehensive testing to ensure the graph handles all workflows correctly.

---

## Validation Criteria

The graph is considered **stable** when:

1. **Core Workflow Completeness** — All nodes execute in sequence without errors
2. **Safety Integration** — Safety checks work correctly and reject dangerous operations
3. **Executor Integration** — Step execution works with retry/fallback logic
4. **Learning Integration** — Historical signals improve routing without breaking it
5. **Error Handling** — All error conditions are handled gracefully
6. **Edge Cases** — Missing data, invalid inputs, and boundary conditions work
7. **State Consistency** — State transitions are correct and predictable
8. **Performance** — Execution time is acceptable for production use

---

## Test Categories

### 1. End-to-End Workflow Tests

**Goal:** Verify complete task execution through all graph nodes

**Test Cases:**
- **Simple Command Execution** — "List files in current directory"
- **Multi-Step Plan** — "Create a file, write to it, then read it"
- **Plugin Execution** — Execute a plugin through the graph
- **UI Automation** — UI-based task execution (if available)
- **API Call** — API-based task execution (if available)

**Expected Outcome:**
- Tasks complete successfully
- State transitions correctly through all nodes
- Execution results are correct
- No unhandled exceptions

---

### 2. Node Integration Tests (Sequential)

**Goal:** Verify each node integrates correctly with the next

**Test Sequence:**
```
intake_node
  ↓
observe_node
  ↓
analyze_intent_node
  ↓
create_plan_node
  ↓
route_node
  ↓
safety_check_node
  ↓
execute_step_node
  ↓
verify_step_node
  ↓
finalize_node
```

**Test Cases:**
- Each node produces valid state for the next node
- State mutations are correct
- History events are recorded
- Trace events are collected
- Conditional edges work correctly

---

### 3. Safety Check Verification

**Goal:** Verify safety integration works correctly

**Test Cases:**
- **Safe Operations** — Low-risk operations are approved
- **High-Risk Operations** — High-risk operations require confirmation
- **Forbidden Operations** — Forbidden patterns are rejected
- **Risk Assessment** — Risk levels are correctly assessed
- **Permission Checks** — Permission checks work correctly
- **Context Validation** — Context validation works correctly

**Expected Outcome:**
- Safe operations approved
- High-risk operations trigger confirmation
- Forbidden patterns rejected
- Risk assessment accurate
- No dangerous operations slip through

---

### 4. Executor Integration Verification

**Goal:** Verify executor integration works correctly

**Test Cases:**
- **Tool Registry Integration** — Tools execute through tool_registry
- **Retry Logic** — Transient errors trigger retries
- **Fallback Logic** — Failed methods trigger fallback chain
- **Error Handling** — Execution errors are handled gracefully
- **Plan Progress** — Plan progress updates on success
- **Performance Feedback** — Performance feedback is collected

**Expected Outcome:**
- Tools execute correctly
- Retries work for transient errors
- Fallbacks work for failed methods
- Errors handled gracefully
- Plan progress tracked correctly
- Performance feedback collected

---

### 5. Learning Integration Verification

**Goal:** Verify learning integration works correctly

**Test Cases:**
- **Performance Feedback Collection** — Performance data collected
- **Historical Ranking Retrieval** — Historical rankings retrieved
- **Learning Adjustments** — Candidate scores adjusted correctly
- **Bounded Adjustments** — Adjustments bounded to ±20%
- **Score Clamping** — Scores clamped to [0, 1]
- **Graceful Degradation** — Works without learning system

**Expected Outcome:**
- Performance data collected
- Historical rankings retrieved
- Adjustments applied correctly
- Adjustments bounded
- Scores clamped
- Works without learning system

---

### 6. Edge Cases and Error Conditions

**Goal:** Verify graph handles edge cases gracefully

**Test Cases:**
- **Missing Plan** — No plan in state
- **Missing Current Step** — No current step in plan
- **Missing Routing Decision** — No routing decision
- **Missing Context** — No context available
- **Missing Intent** — No intent available
- **Invalid Parameters** — Invalid step parameters
- **Empty Candidates** — No candidates discovered
- **Tool Registry Unavailable** — tool_registry import fails
- **Learning System Unavailable** — learning_integration import fails
- **Safety Modules Unavailable** — Safety modules import fails

**Expected Outcome:**
- All edge cases handled gracefully
- Fallback logic works
- No crashes or unhandled exceptions
- Appropriate error messages

---

### 7. State Consistency Tests

**Goal:** Verify state transitions are correct and predictable

**Test Cases:**
- **Initial State** — State initialized correctly
- **State Mutations** — Each node mutates state correctly
- **State History** — History events recorded correctly
- **State Timestamps** — Timestamps updated correctly
- **State Serialization** — State can be serialized/deserialized
- **State Recovery** — State can be recovered from checkpoint

**Expected Outcome:**
- State initialized correctly
- Mutations correct
- History recorded
- Timestamps updated
- Serialization works
- Recovery works

---

### 8. Performance Tests

**Goal:** Verify performance is acceptable for production

**Test Cases:**
- **Single Step Execution** — Time to execute one step
- **Multi-Step Execution** — Time to execute multi-step plan
- **Candidate Discovery** — Time to discover candidates
- **Safety Check** — Time to perform safety check
- **Execution** — Time to execute step
- **Overall Workflow** — End-to-end workflow time

**Expected Outcome:**
- Performance acceptable for production
- No significant bottlenecks
- Resource usage reasonable

---

## Validation Checklist

### Core Workflow
- [ ] Simple command execution works
- [ ] Multi-step plan execution works
- [ ] Plugin execution works
- [ ] UI automation works (if available)
- [ ] API execution works (if available)

### Node Integration
- [ ] intake_node → observe_node works
- [ ] observe_node → analyze_intent_node works
- [ ] analyze_intent_node → create_plan_node works
- [ ] create_plan_node → route_node works
- [ ] route_node → safety_check_node works
- [ ] safety_check_node → execute_step_node works
- [ ] execute_step_node → verify_step_node works
- [ ] verify_step_node → finalize_node works

### Safety Checks
- [ ] Safe operations approved
- [ ] High-risk operations require confirmation
- [ ] Forbidden patterns rejected
- [ ] Risk assessment accurate
- [ ] Permission checks work
- [ ] Context validation works

### Executor Integration
- [ ] Tool registry integration works
- [ ] Retry logic works
- [ ] Fallback logic works
- [ ] Error handling works
- [ ] Plan progress updates
- [ ] Performance feedback collected

### Learning Integration
- [ ] Performance feedback collected
- [ ] Historical rankings retrieved
- [ ] Learning adjustments applied
- [ ] Adjustments bounded
- [ ] Scores clamped
- [ ] Graceful degradation works

### Edge Cases
- [ ] Missing plan handled
- [ ] Missing step handled
- [ ] Missing routing handled
- [ ] Missing context handled
- [ ] Missing intent handled
- [ ] Invalid parameters handled
- [ ] Empty candidates handled
- [ ] Tool registry unavailable handled
- [ ] Learning system unavailable handled
- [ ] Safety modules unavailable handled

### State Consistency
- [ ] Initial state correct
- [ ] State mutations correct
- [ ] History events recorded
- [ ] Timestamps updated
- [ ] Serialization works
- [ ] Recovery works

### Performance
- [ ] Single step execution acceptable
- [ ] Multi-step execution acceptable
- [ ] Candidate discovery acceptable
- [ ] Safety check acceptable
- [ ] Execution acceptable
- [ ] Overall workflow acceptable

---

## Validation Tools

### Test Files
- `tests/test_graph_stability.py` — End-to-end workflow tests
- `tests/test_node_integration.py` — Sequential node integration tests
- `tests/test_edge_cases.py` — Edge case and error condition tests

### Validation Script
- `scripts/validate_graph_stability.py` — Automated validation runner

### Manual Validation
- Run canonical workflows manually
- Monitor logs for errors
- Check trace events
- Verify state consistency

---

## Success Criteria

The graph is considered **stable** when:

1. **All validation tests pass** — 100% test pass rate
2. **No unhandled exceptions** — All errors handled gracefully
3. **State consistency maintained** — State transitions predictable
4. **Performance acceptable** — Execution time within acceptable bounds
5. **Safety checks effective** — No dangerous operations slip through
6. **Executor integration works** — Retry/fallback logic works correctly
7. **Learning integration works** — Historical signals improve routing

---

## Next Steps

1. **Create validation tests** — Implement test files
2. **Run validation tests** — Execute all tests
3. **Analyze results** — Review test results and logs
4. **Fix issues** — Address any failures or issues
5. **Re-validate** — Re-run tests after fixes
6. **Document results** — Create stability validation report
7. **Proceed to Phase 15** — If stable, proceed with legacy retirement

---

## Notes

- Validation should be done in a test environment first
- Use realistic test cases that mirror production workflows
- Monitor resource usage during validation
- Keep detailed logs for analysis
- Document any issues found and their resolution
