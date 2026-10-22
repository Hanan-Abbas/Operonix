# Phase 5: Remove Domain Restrictions - Analysis

## Overview
Analysis of current implementation vs Phase 5 requirements.

---

## 5.1 LLM Generalization

### Current State
✅ **ALREADY IMPLEMENTED (Phase 1)**

**Evidence:**
- `graph/nodes/create_plan.py` has `_generate_command_with_llm()` function
- LLM generates concrete commands based on training data
- Platform-aware prompts (Linux/GNOME/KDE, macOS, Windows)
- No hardcoded command list
- Can generate ANY valid system command
- `command_translators.py` is deprecated

**Test Result:**
- LLM correctly generated `gnome-system-monitor` for "open system monitor"
- No domain restrictions - LLM can generate any valid command

### Status
✅ **COMPLETE** - No changes needed

---

## 5.2 Candidate Discovery Generalization

### Current State
⚠️ **PARTIALLY IMPLEMENTED**

**What Exists:**
- `CandidateType` enum includes: PLUGIN, API, SHELL, UI, BROWSER_AUTOMATION, VISION, REMOTE, LOCAL
- Registration methods: `register_plugin()`, `register_api()`, `register_tool()`
- Discovery methods for each type:
  - `_discover_shell_candidates()`
  - `_discover_api_candidates()`
  - `_discover_plugin_candidates()`
  - `_discover_ui_candidates()`
  - `_discover_browser_candidates()`
  - `_discover_vision_candidates()`

**The Problem:**
- To add a new method type (e.g., MCP), you must:
  1. Add to `CandidateType` enum
  2. Create a new `_discover_*()` method
  3. Add call to `discover_candidates()`
- **Not fully extensible** - requires code changes for new method types
- Hardcoded list of discovery methods in `discover_candidates()`

**What's Needed:**
- Generic registration mechanism for new method types
- Plugin-like discovery where new methods can register themselves
- No hardcoded method type list in `discover_candidates()`
- New methods automatically participate in candidate-based routing

### Proposed Solution
Add a generic `register_candidate_discovery()` method that allows registering custom discovery functions:

```python
def register_candidate_discovery(
    self,
    method_type: str,
    discovery_func: Callable[[PlanStep, IntentResult, Dict[str, Any]], List[Candidate]]
) -> None:
    """Register a custom candidate discovery function for a method type."""
    self.custom_discovery_functions[method_type] = discovery_func
```

Then in `discover_candidates()`, iterate over registered discovery functions instead of hardcoded calls.

### Status
⚠️ **NEEDS ENHANCEMENT** - Add generic registration mechanism

---

## 5.3 Plan Generation Generalization

### Current State
✅ **ALREADY IMPLEMENTED (Phase 1)**

**Evidence:**
- `graph/nodes/create_plan.py` has LLM-based plan generation
- `_generate_simple_plan()` uses LLM for simple requests
- `_generate_plan_with_langchain()` uses LLM for complex requests
- LLM breaks down ANY request into steps
- No predefined plan patterns
- LLM reasoning handles novel scenarios

**Test Result:**
- LLM generates appropriate plans based on request complexity
- No hardcoded plan patterns

### Status
✅ **COMPLETE** - No changes needed

---

## Summary

### What's Already Done
✅ 5.1 LLM Generalization - Complete (Phase 1)
✅ 5.3 Plan Generation Generalization - Complete (Phase 1)

### What Needs Enhancement
⚠️ 5.2 Candidate Discovery Generalization - Needs generic registration mechanism

### Implementation Plan for 5.2

**Step 5.2.1: Add Generic Discovery Registration**
- Add `register_candidate_discovery()` method to `CandidateDiscoveryService`
- Store custom discovery functions in a dictionary
- Allow new method types to register without code changes

**Step 5.2.2: Update discover_candidates()**
- Remove hardcoded discovery method calls
- Iterate over registered discovery functions
- Include built-in methods (shell, api, plugin, ui, browser, vision) in registration
- Call all registered discovery functions

**Step 5.2.3: Test Extensibility**
- Test that a new method type can be registered
- Test that registered discovery functions are called
- Test that new methods participate in candidate-based routing

### Success Criteria
- ✅ Works for arbitrary commands (already done)
- ⚠️ New execution methods can be added without code changes (needs enhancement)
- ✅ LLM handles novel scenarios (already done)
- ⚠️ No domain restrictions (needs candidate discovery enhancement)

### Recommendation
Only implement Step 5.2 (Candidate Discovery Generalization) since 5.1 and 5.3 are already complete.
