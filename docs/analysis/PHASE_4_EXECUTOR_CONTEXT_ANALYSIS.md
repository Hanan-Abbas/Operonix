# Phase 4: Executor Context Analysis

## Overview
This document analyzes the executor module before implementing Phase 4 changes to ensure we don't break existing functionality.

---

## Current Executor Architecture

### Entry Points

**1. `execute_plan(event)` (Line 284-324)**
- Main entry point for task execution
- Subscribes to `task_dispatched_safe` event
- Dispatches tasks as background asyncio.Task for concurrency
- Dedup guard prevents duplicate task execution

**2. `_run_plan(event)` (Line 328-496)**
- Plan runner that iterates through steps
- Enriches context with cwd and profile_hint
- Two execution paths:
  - **New path**: `_execute_with_decision()` (when MethodDecision present)
  - **Legacy path**: `_execute_step_safe()` (when MethodDecision absent)

### Execution Paths

**Path A: MethodDecision-Driven Execution**
- **Location**: Lines 404-408
- **Condition**: `method_decision is not None`
- **Call**: `await self._execute_with_decision(task_id, step_index, step, context, method_decision)`
- **Status**: This is the graph mode path

**Path B: Legacy Capability Waterfall**
- **Location**: Lines 410-414
- **Condition**: `method_decision is None`
- **Call**: `await self._execute_step_safe(task_id, step_index, step, context, preferred_method)`
- **Status**: This is the legacy mode path

---

## Key Executor Components

### 1. `_execute_with_decision()` (Line 548-755)

**Current Behavior:**
- **Phase 3 Added**: Checks `USE_GRAPH_ROUTING` flag
- **When USE_GRAPH_ROUTING=true**:
  - Does NOT walk fallback_chain
  - Returns routing-level failures to graph
  - Only retries execution-level failures
- **When USE_GRAPH_ROUTING=false**:
  - Walks fallback_chain on ROUTING_MISMATCH and ENV_PERMANENT
  - Retries ENV_TRANSIENT failures
  - Legacy behavior

**Failure Classification (via error_classifier):**
- ENV_TRANSIENT → retry same method
- ROUTING_MISMATCH → advance fallback_chain, publish routing_mismatch
- ENV_PERMANENT → mark unavailable, advance fallback_chain
- EXECUTION_LOGIC → no retry, no fallback, surface to debugger

**Phase 3 Changes Already Applied:**
- Lines 571-574: Added `use_graph_routing` check
- Lines 576-632: Main execution loop
- Lines 715-839: `_handle_failure()` with graph mode conditional
- Lines 772-785: ROUTING_MISMATCH handling with graph mode conditional
- Lines 807-833: ENV_PERMANENT handling with graph mode conditional

### 2. `_execute_step_safe()` (Line 1099-1194+)

**Current Behavior:**
- Legacy execution path for tasks without MethodDecision
- Uses capability waterfall (plugin → api → command → ui)
- Handles focus management
- Handles profile resolution and injection
- **Status**: Preserved for backward compatibility

**Key Features:**
- Direct plugin dispatch via intent matching
- Shell action handling with profile resolution
- Focus management for UI actions
- **This should NOT be modified** - it's the legacy fallback

### 3. Error Classifier (`executor/error_classifier.py`)

**Current Behavior:**
- Classifies exceptions into FailureClass types
- Used by `_execute_with_decision()` to tag failures
- Already integrated and working

### 4. Fallback Manager (`executor/fallback_manager.py`)

**Current Behavior:**
- Manages fallback chain state
- Tracks which methods have been tried
- Returns next fallback method
- **Status**: Only used in legacy mode when `USE_GRAPH_ROUTING=false`

### 5. Retry Manager (`executor/retry_manager.py`)

**Current Behavior:**
- Manages retry logic for transient failures
- Implements backoff strategy
- **Status**: Used for execution-level retries

---

## Recovery Node Analysis

### `recover_node()` (Line 21-81)

**Current Behavior:**
- Classifies failure type via `_classify_failure()`
- Determines recovery strategy via `_determine_recovery_strategy()`
- Maps failure categories to recovery strategies:
  - TRANSIENT → retry
  - CONTEXT_MISMATCH → observe
  - ROUTING_MISMATCH → re-route
  - PLANNING_ERROR → re-plan
  - TOOL_UNAVAILABLE → re-route
  - PERMISSION_DENIED → abort
  - VALIDATION_REJECTED → abort
  - ENVIRONMENTAL → abort
  - PERMANENT → abort
  - UNKNOWN → abort

**Failure Classification** (Line 84-144):
- Currently classifies based on verification status and reason string
- Matches keywords in reason string (transient, context, routing, etc.)
- **Status**: Basic string-based classification

**Recovery Strategy Mapping** (Line 147-211):
- Already implements the required mapping from migration plan §4.5
- Checks idempotency and side-effect level before retry
- **Status**: Mostly complete, may need enhancement

**Target Stage Mapping** (Line 266-292):
- RETRY → execute_step
- OBSERVE → observe
- ROUTE → route
- REPLAN → create_plan
- ABORT → finalize
- **Status**: Correctly implemented

---

## Phase 4 Requirements vs Current State

### 4.1 Update Executor

**Requirement:**
- Remove executor's internal routing decision-making
- Accept MethodDecision from graph as authoritative
- Only use internal retry/fallback for execution-level failures
- On routing-level failure, return error to graph → graph re-routes

**Current State:**
- ✅ Executor already accepts MethodDecision as authoritative (Line 358)
- ✅ Executor already has two paths (MethodDecision vs legacy)
- ✅ Phase 3 already added `USE_GRAPH_ROUTING` conditional to skip fallback_chain
- ✅ Phase 3 already returns routing-level failures to graph (Lines 772-785, 807-833)
- ⚠️ Legacy path `_execute_step_safe()` still has internal routing (waterfall)

**What's Needed:**
- ✅ **ALREADY DONE** for graph mode path
- Legacy path should remain unchanged (backward compatibility)
- No changes needed to executor for Phase 4

### 4.2 Distinguish Failure Types

**Requirement:**
- Execution-level failure: Command started but crashed → executor retries
- Routing-level failure: Wrong method chosen → graph re-routes
- Context-level failure: Context changed → graph re-observes
- Plan-level failure: Wrong planning assumption → graph re-plans
- Add failure classification in executor
- Return specific failure category in ExecutionResult
- Graph's recover node routes to correct upstream stage based on category

**Current State:**
- ✅ Executor already uses error_classifier for FailureClass (ENV_TRANSIENT, ROUTING_MISMATCH, etc.)
- ✅ Executor already tags failures with FailureClass
- ✅ Executor already returns error type via EventBus events
- ⚠️ FailureClass is executor-internal, not returned in ExecutionResult to graph
- ⚠️ Recovery node classifies based on verification reason string, not executor's FailureClass

**What's Needed:**
- Add failure category to ExecutionResult (or graph state)
- Connect executor's FailureClass to graph's FailureCategory
- Enhance recovery node to use executor's FailureClass instead of string matching

### 4.3 Update `recover` Node

**Requirement:**
- Route to specific upstream stage based on failure category
- Don't use universal fallback chain

**Current State:**
- ✅ Already routes to specific upstream stages (Line 266-292)
- ✅ Already implements failure category classification (Line 84-144)
- ✅ Already implements recovery strategy mapping (Line 147-211)
- ⚠️ Classification is string-based, not using executor's FailureClass
- ⚠️ May need enhancement for edge cases

**What's Needed:**
- Connect executor's FailureClass to recovery node's classification
- Enhance classification to use executor-provided failure data

---

## Critical Components to Preserve

### MUST NOT BREAK:

1. **Legacy Execution Path** (`_execute_step_safe()`)
   - Used when `USE_LANGGRAPH=false` or `USE_GRAPH_ROUTING=false`
   - Must remain unchanged for backward compatibility
   - Contains capability waterfall logic
   - **Do not modify**

2. **EventBus Event Publishing**
   - Executor publishes events for learning and observability
   - Events: `tool_selected`, `routing_mismatch`, `method_unavailable`, etc.
   - **Must preserve event structure**

3. **Dedup Guard** (`_seen_task_ids`)
   - Prevents duplicate task execution
   - **Must preserve**

4. **Concurrent Execution Registry** (`_running_tasks`)
   - Tracks background tasks
   - **Must preserve**

5. **Error Classifier Integration**
   - Already integrated and working
   - **Must preserve**

---

## Implementation Strategy for Phase 4

### Step 4.1: Enhance Failure Classification Communication

**Problem:**
- Executor's FailureClass is internal, not communicated to graph
- Recovery node uses string matching on verification reason

**Solution:**
- Add failure category to ExecutionResult or graph state
- Pass executor's FailureClass through to graph state
- Update recovery node to use FailureClass instead of string matching

**Files to Modify:**
- `migration/domain_contracts.py` - Add failure category to ExecutionResult
- `executor/executor.py` - Pass FailureClass to ExecutionResult/graph state
- `graph/nodes/recover.py` - Use FailureClass for classification

### Step 4.2: Verify Executor Doesn't Make Routing Decisions

**Problem:**
- Ensure executor only executes, doesn't route

**Solution:**
- Verify `_execute_with_decision()` only uses MethodDecision from graph
- Verify no internal routing logic in graph mode
- Verify fallback_chain is skipped in graph mode (already done in Phase 3)

**Files to Modify:**
- None needed (Phase 3 already implemented this)

### Step 4.3: Enhance Recovery Node

**Problem:**
- Recovery node uses string matching for classification

**Solution:**
- Update `_classify_failure()` to use executor's FailureClass
- Add better failure type detection
- Ensure mapping to recovery strategies is correct

**Files to Modify:**
- `graph/nodes/recover.py` - Enhance `_classify_failure()`

---

## Risk Assessment

### High Risk:
- **NONE** - All changes are additive, not breaking

### Medium Risk:
- **Modifying ExecutionResult schema** - May require database migration
- **Changing recovery classification logic** - May affect existing recovery behavior

### Low Risk:
- **Adding failure category to state** - Purely additive
- **Enhancing recovery node** - Logic improvement only

---

## Recommendation

**Phase 4 should be implemented incrementally:**

1. **Step 4.1:** Add failure category to ExecutionResult and graph state
2. **Step 4.2:** Verify executor routing authority (Phase 3 already done)
3. **Step 4.3:** Enhance recovery node to use FailureClass

**Do NOT modify:**
- `_execute_step_safe()` - Legacy path must remain unchanged
- EventBus event structure - Must preserve for learning/observability
- Error classifier - Already working correctly

**Next Action:**
Implement Step 4.1 (Add failure category to ExecutionResult)
