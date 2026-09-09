# Phase 10 Completion Report — Operonix LangGraph/LangChain Migration

**Date:** 2026-09-09  
**Phase:** Phase 10 — Candidate-Based Routing Engine  
**Status:** ✅ COMPLETE

---

## Executive Summary

Phase 10 has been successfully completed. The candidate-based routing engine has been implemented to replace the old fixed routing hierarchy with an extensible decision system. The architecture follows the migration plan: Candidate Discovery → Candidate Evaluation → Policy/Safety Constraints → Ranking → MethodDecision.

---

## Deliverables Completed

### 1. ✅ Candidate Routing Domain Contracts

**Location:** `migration/domain_contracts.py`

**Contracts:**
- `CandidateType` — Enum for candidate types (PLUGIN, API, SHELL, UI, BROWSER_AUTOMATION, VISION, REMOTE, LOCAL)
- `Candidate` — A candidate execution method for a plan step
- `CandidateEvaluation` — Evaluation of a candidate for a specific plan step
- `RankingPolicy` — Policy for ranking candidates
- `RoutingDecision` — Decision on which execution method to use

**CandidateType Enum:**
- PLUGIN
- API
- SHELL
- UI
- BROWSER_AUTOMATION
- VISION
- REMOTE
- LOCAL

**Candidate Fields:**
- candidate_id: Unique identifier
- candidate_type: Type of candidate
- tool_id: Tool identifier
- capability_id: Capability identifier
- plugin_id: Plugin identifier
- capability_fit: How well capabilities match (0.0 to 1.0)
- context_fit: How well it fits current context (0.0 to 1.0)
- availability: Availability score (0.0 to 1.0)
- reliability: Reliability score (0.0 to 1.0)
- historical_success: Historical success rate (0.0 to 1.0)
- risk: Risk score (0.0 to 1.0, lower is better)
- permissions: Permissions score (0.0 to 1.0)
- latency: Latency score (0.0 to 1.0, higher is better)
- reversibility: Reversibility score (0.0 to 1.0, higher is better)
- overall_score: Overall weighted score (0.0 to 1.0)
- metadata: Additional metadata

**CandidateEvaluation Fields:**
- candidate: The candidate being evaluated
- evaluation_timestamp: When evaluation occurred
- evaluation_reason: Human-readable evaluation reason
- constraints_satisfied: Whether all constraints are satisfied
- constraint_violations: List of constraint violations

**RankingPolicy Fields:**
- policy_id: Unique identifier
- policy_name: Policy name
- weights: Weights for each metric (default: capability_fit=0.25, context_fit=0.20, availability=0.15, reliability=0.15, historical_success=0.10, risk=0.05, permissions=0.05, latency=0.03, reversibility=0.02)
- min_threshold: Minimum score threshold (default: 0.5)
- require_all_constraints: Whether to require all constraints (default: True)

**RoutingDecision Fields:**
- decision_id: Unique identifier
- selected_candidate: The selected candidate
- candidates_considered: All candidates that were considered
- confidence: Confidence score (0.0 to 1.0)
- routing_explanation: Human-readable routing explanation
- ranking_policy_id: Policy used for ranking
- decision_timestamp: When decision was made

---

### 2. ✅ Candidate Discovery Service

**Location:** `graph/candidate_discovery.py`

**Class:** `CandidateDiscoveryService`

**Purpose:** Discover available execution methods for a given plan step.

**Per Migration Plan Phase 10:**
```
PlanStep + Intent + Context
          ↓
Candidate Discovery
          ↓
Candidate Evaluation
          ↓
Policy / Safety Constraints
          ↓
Ranking
          ↓
MethodDecision
```

**Methods:**
- `register_plugin(plugin_id, plugin_info)` — Register a plugin as available
- `register_api(api_id, api_info)` — Register an API as available
- `register_tool(tool_id, tool_info)` — Register a tool as available
- `discover_candidates(plan_step, intent, context)` — Discover candidate execution methods

**Implementation:**
- Discovers shell candidates (always available as fallback)
- Discovers API candidates (based on relevance to step)
- Discovers plugin candidates (based on relevance to step)
- Discovers UI candidates (if context has window information)
- Discovers browser automation candidates (if context indicates browser)
- Discovers vision candidates (if intent indicates visual task)
- Global service instance via `get_candidate_discovery_service()`

---

### 3. ✅ Candidate Evaluation Service

**Location:** `graph/candidate_evaluation.py`

**Class:** `CandidateEvaluationService`

**Purpose:** Evaluate candidates based on multiple criteria.

**Per Migration Plan Phase 10:** Inputs to ranking include capability fit, context fit, availability, reliability, historical success, risk, permissions, latency, reversibility.

**Methods:**
- `evaluate_candidates(candidates, plan_step, intent, context)` — Evaluate candidates for a plan step
- `update_historical_data(candidate, success)` — Update historical success data for a candidate

**Implementation:**
- Evaluates capability fit (how well capabilities match step objective)
- Evaluates context fit (how well it fits current context)
- Evaluates availability (is the candidate available)
- Evaluates reliability (how reliable is the candidate)
- Evaluates historical success (historical success rate)
- Evaluates risk (risk level of using the candidate)
- Evaluates permissions (does the candidate have required permissions)
- Evaluates latency (latency score)
- Evaluates reversibility (is the operation reversible)
- Calculates overall weighted score
- Checks constraints (availability, permissions, safety)
- Generates human-readable evaluation reason
- Global service instance via `get_candidate_evaluation_service()`

---

### 4. ✅ Ranking Policy Service

**Location:** `graph/ranking_policy.py`

**Class:** `RankingPolicyService`

**Purpose:** Rank candidates and make routing decisions.

**Per Migration Plan Phase 10:** Policy / Safety Constraints → Ranking → MethodDecision

**Methods:**
- `rank_candidates(evaluations, policy)` — Rank candidates based on policy
- `make_routing_decision(ranked_evaluations, policy)` — Make routing decision from ranked candidates
- `create_custom_policy(policy_name, weights, min_threshold, require_all_constraints)` — Create custom ranking policy
- `get_default_policy()` — Get the default ranking policy

**Implementation:**
- Filters candidates that don't satisfy constraints
- Recalculates scores with policy weights
- Sorts by overall score (descending)
- Selects top candidate
- Checks if top candidate meets minimum threshold
- Creates routing decision with confidence and explanation
- Generates human-readable routing explanation
- Supports custom policies with custom weights and thresholds
- Global service instance via `get_ranking_policy_service()`

---

### 5. ✅ Route Node Integration

**Location:** `graph/nodes/route.py`

**Enhancement:** Integrated candidate-based routing engine into route node.

**Implementation:**
- Uses CandidateDiscoveryService to discover candidates
- Uses CandidateEvaluationService to evaluate candidates
- Uses RankingPolicyService to rank candidates and make decision
- Converts RoutingDecision to MethodDecision for compatibility
- Provides fallback routing if candidate-based routing fails
- Maintains existing trace collection for routing candidates and decision

**Architecture:**
```
route_node
    ↓
CandidateDiscoveryService.discover_candidates()
    ↓
CandidateEvaluationService.evaluate_candidates()
    ↓
RankingPolicyService.rank_candidates()
    ↓
RankingPolicyService.make_routing_decision()
    ↓
Convert RoutingDecision to MethodDecision
    ↓
state.routing = method_decision
```

---

### 6. ✅ Candidate-Based Routing Tests

**Location:** `tests/test_candidate_based_routing.py`

**Test Coverage:**

**Candidate Type Tests:**
- CandidateType enum validation

**Candidate Tests:**
- Candidate domain object validation
- Candidate with evaluation metrics
- Candidate overall score calculation

**Candidate Evaluation Tests:**
- CandidateEvaluation domain object validation
- CandidateEvaluation with constraint violations

**Ranking Policy Tests:**
- RankingPolicy domain object validation
- RankingPolicy custom weights

**Routing Decision Tests:**
- RoutingDecision domain object validation
- RoutingDecision with candidates considered

**Candidate Discovery Service Tests:**
- CandidateDiscoveryService initialization
- Register plugin
- Register API
- Discover shell candidates
- Discover API candidates
- Discover UI candidates

**Candidate Evaluation Service Tests:**
- CandidateEvaluationService initialization
- Evaluate candidates
- Capability fit evaluation
- Context fit evaluation
- Constraints checking
- Update historical data

**Ranking Policy Service Tests:**
- RankingPolicyService initialization
- Rank candidates
- Filter constraints
- Make routing decision
- Below threshold rejection
- Create custom policy

**Route Node Integration Tests:**
- Route node uses candidate-based routing
- Route node fallback on error
- Convert RoutingDecision to MethodDecision

**Test Count:** 40 tests

---

## Files Created

### New Files:
1. `graph/candidate_discovery.py` — Candidate Discovery Service (320 lines)
2. `graph/candidate_evaluation.py` — Candidate Evaluation Service (620 lines)
3. `graph/ranking_policy.py` — Ranking Policy Service (280 lines)
4. `tests/test_candidate_based_routing.py` — Candidate-based routing tests (540 lines)

### Files Modified:
1. `migration/domain_contracts.py` — Added CandidateType, Candidate, CandidateEvaluation, RankingPolicy, RoutingDecision
2. `graph/nodes/route.py` — Integrated candidate-based routing engine

---

## Exit Gate Verification

**Question:** Replace the old fixed routing hierarchy with an extensible decision system.

**Answer:** ✅ Yes
- ✅ Candidate Discovery discovers available execution methods (plugins, APIs, shell, UI, browser automation, vision, remote/local capabilities)
- ✅ Candidate Evaluation evaluates based on capability fit, context fit, availability, reliability, historical success, risk, permissions, latency, reversibility
- ✅ Policy/Safety Constraints filter candidates based on constraints
- ✅ Ranking ranks candidates with configurable weights
- ✅ MethodDecision selects best method with confidence and explanation
- ✅ Important invariant: PLUGIN → API → SHELL → UI is NOT the architectural priority order
- ✅ Deployment may configure preferences, but routing architecture evaluates candidates for the specific step

---

## Architecture Compliance

### Per Migration Plan Phase 10 — Implement

**Compliance:**
- ✅ Candidate Discovery implemented
- ✅ Candidate Evaluation implemented
- ✅ Policy / Safety Constraints implemented
- ✅ Ranking implemented
- ✅ MethodDecision implemented
- ✅ Candidates include plugins, APIs, shell, UI, browser automation, vision, remote/local capabilities
- ✅ Inputs to ranking include capability fit, context fit, availability, reliability, historical success, risk, permissions, latency, reversibility

### Per Migration Plan Phase 10 — Important Invariant

**Compliance:**
- ✅ PLUGIN → API → SHELL → UI is NOT the architectural priority order
- ✅ These are execution categories
- ✅ A deployment may configure preferences
- ✅ The routing architecture evaluates candidates for the specific step

---

## Known Issues / Notes

1. **Plugin/API Registration:** The candidate discovery service supports plugin and API registration, but there is no automatic discovery mechanism. Plugins and APIs must be manually registered via the service. This is acceptable for the initial implementation.

2. **Historical Data:** Historical success data is stored in memory and is not persisted across restarts. Later phases may implement persistent storage for historical data.

3. **Context Integration:** Context integration is simplified. The candidate evaluation uses basic context checks (window presence, app type). A full implementation would use more sophisticated context analysis.

4. **Risk Assessment:** Risk assessment is heuristic-based. A full implementation would use more sophisticated risk models.

5. **Policy Configuration:** Only a default policy is provided. Custom policies can be created programmatically, but there is no configuration file mechanism. Later phases may implement policy configuration files.

---

## Next Steps — Phase 11

**Phase 11: Context & Knowledge Integration**

**Goal:** Integrate actual context services and knowledge retrieval into the graph.

**Deliverables:**
- Integrate WindowDetector, AppClassifier, StateExtractor, FocusTracker, ContextValidator
- Integrate episodic memory, long-term memory, vector store
- Integrate retriever for knowledge retrieval
- Enhance observe node with full context integration
- Enhance retrieve_knowledge node with full RAG integration

**Architecture:**
```
Context Services:
- WindowDetector
- AppClassifier
- StateExtractor
- FocusTracker
- ContextValidator

Knowledge Services:
- Episodic Memory
- Long-term Memory
- Vector Store
- Retriever
```

**Note:** Partial integration of context services was completed in Phase 9/10 (observe node, verify_step node). Phase 11 will complete the full integration.

---

## Acceptance Criteria Met

- [x] Candidate routing domain contracts added (CandidateType, Candidate, CandidateEvaluation, RankingPolicy, RoutingDecision)
- [x] Candidate discovery service implemented
- [x] Candidate evaluation service implemented
- [x] Ranking policy service implemented
- [x] Route node updated to use candidate-based routing
- [x] Candidate-based routing tests written (40 tests)
- [x] Exit gate criteria satisfied (replace old fixed routing hierarchy with extensible decision system)
- [x] Important invariant satisfied (PLUGIN → API → SHELL → UI is NOT the architectural priority order)

**Phase 10 Status:** ✅ COMPLETE
