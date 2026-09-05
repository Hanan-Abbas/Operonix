# Operonix → LangGraph/LangChain Migration Plan

## 0. Executive Summary

Operonix currently implements four kinds of orchestration in an intertwined way:

1. **Event orchestration** — `EventBus` + `Orchestrator`
2. **AI orchestration** — `IntentParser → CapabilityMapper → DecisionEngine → Planner`
3. **Execution orchestration** — `ToolSelector → Executor → Retry/Fallback → Capability`
4. **Feedback/learning** — `Executor → execution_complete → Reflector → Memory/Learning/Evolution`

`core/orchestrator.py` has become an **implicit, manually-maintained state machine**: it subscribes to ten-plus event types and hand-rolls task state in `active_tasks[task_id]`. That is the clearest signal that the system is ready for a graph-based workflow engine.

**The core move is not "replace Operonix with LangGraph."** It's:

```
CURRENT                          FUTURE
EventBus                         EventBus
   ↓                                ↓
Orchestrator          →        Operonix Runtime
   ↓                                ↓
many event-driven            LangGraph (task workflow)
AI/execution stages               ↓
                              AI workflow (LangChain)
```

- **EventBus** keeps owning system-wide communication (dashboard, logs, metrics, plugin lifecycle, learning notifications).
- **LangGraph** owns the lifecycle of a *single task* — its state, sequencing, branching, retries-at-workflow-level, and replanning.
- **LangChain** owns AI capability (structured LLM output, tool-calling interface, RAG).
- Everything else Operonix already does well (Executor, Safety, Context, Capabilities, Plugins, Memory) **stays as services** the graph calls into — it is not rewritten wholesale.

---

## 1. Current Real Flow (as found in source)

```
User
 │
 ▼
core/orchestrator ──► Context snapshot
 │
 ▼
LLMClient → IntentParser → CapabilityMapper → DecisionEngine → Planner
 │
 ▼
MethodRouter → task_dispatched → PermissionGuard → SafetyValidator
 │
 ▼
Executor
 ├── ToolRegistry
 ├── ToolSelector
 ├── RetryManager
 ├── FallbackManager
 └── FocusManager
 │
 ▼
Capabilities / Plugins → Automation Engine → execution_complete
 │
 ▼
Reflector
 ├── EpisodicMemory
 ├── LongTermMemory
 └── evolution_needed
```

This is considerably more sophisticated than the README's simplified diagram, and it's a strong foundation for LangGraph — the pieces already exist, they're just event-glued instead of state-explicit.

### Key finding: `core/orchestrator.py` does too much

It currently coordinates: input, task creation, context, intent parsing, capability mapping, method routing, safety dispatch, executor dispatch, completion, failure, and reflector initialization — by subscribing to `wake_word_detected`, `text_query_received`, `user_input_received`, `context_snapshot_ready`, `intent_parsed`, `capability_mapped`, `task_failed`, `task_completed`, `task_safety_cleared`, `confirmation_required`.

This isn't a defect — it's evidence the orchestrator is *already* an implicit state machine. `active_tasks[task_id]` (holding `status`, `input`, `source`, `preferred_method`, `profile_hint`, `cwd`, `context`, `started_at`, `intent`, …) is a manually implemented graph state.

### EventBus vs. LangGraph — the rule going forward

| | Handles |
|---|---|
| **EventBus** | system events, plugin events, dashboard events, lifecycle events, metrics, logging, external integrations |
| **LangGraph** | one task's state: intent, planning, tool selection, execution, verification, retry, replanning, completion |

```
                 Operonix Runtime
                       │
            ┌──────────┴──────────┐
            ▼                     ▼
        EventBus             LangGraph
            │                     │
     system events          task workflow
            │                     │
            └──────────┬──────────┘
                        ▼
                    Executor
```

This prevents LangGraph from swallowing the entire event architecture.

### Notable inventory findings

- `brain/goal_stack.py` is **currently empty** — nothing to preserve; `plan.steps` / `current_step` / `completed_steps` in graph state replaces it outright.
- `DecisionEngine`, `ToolSelector`, `MethodRouter`, `CapabilityMapper` already have **overlapping routing responsibility** — this is the main duplication to clean up during migration.
- `ToolValidator` already has a two-layer model (SafetyGuard → SemanticValidator) — a strong pattern to keep.
- `Reflector` already has clean domain concepts (`OutcomeGrade`, `FailureCategory`, `Lesson`, `ReflectionStats`) that map almost directly onto a `reflect` graph node.

---

## 2. Target Architecture at a Glance

```
                       OPERONIX
                           │
          ┌────────────────┼────────────────┐
          │                │                │
          ▼                ▼                ▼
      ORCHESTRATION    INTELLIGENCE      EXECUTION
      LangGraph        LangChain        Operonix
          │                │                │
          │                │                ├─ Executor
          │                │                ├─ Capabilities
          │                │                ├─ Plugins
          │                │                └─ Automation
          │                │
          │                ├─ Models
          │                ├─ Structured output
          │                ├─ RAG
          │                └─ AI-facing tools
          │
          ├─ State
          ├─ Nodes
          ├─ Routing
          ├─ Recovery
          └─ Reflection
```

Safety, Context, Memory, Learning, and EventBus cut across all three layers as supporting systems.

**Three kinds of decisions, three owners:**

| Decision type | Question | Owner |
|---|---|---|
| AI decision | "What does the user want? What might accomplish it? How should a complex task be planned?" | LangChain / LLM |
| Deterministic operational decision | "Which execution layer is allowed/available? Is this risky? Can this plugin run?" | Operonix Python (Safety, Routing) |
| Workflow decision | "What node happens next? Retry? Replan? Re-observe? Finish?" | LangGraph |

---

## 3. Part A — Proposed `OperonixState`

Graph state is **workflow state for one running task** — not the whole system's knowledge. It is *not* memory, *not* the context subsystem, *not* the EventBus, and *not* a place to store service objects.

```
OperonixState
│
├── task            (task_id, user_input, source, created_at, status)
├── intent          (name, confidence, parameters, profile_hint)
├── context         (active_window, app, app_type, window_title, cwd,
│                     sub_context, ui_state, permissions, confidence)
├── knowledge       (retrieved_memories, retrieved_documents, learned_patterns)
├── plan            (steps, current_step_index, completed_steps)
├── routing         (method_decision, selected_tool, fallback_chain,
│                     confidence, decision_log)
├── safety          (risk_level, validation_status, permission_status,
│                     confirmation_required, confirmation_reason, user_decision)
├── execution       (current_step, current_action, current_arguments,
│                     attempt, result, method_used, execution_status)
├── history         (step_results, tool_calls, errors, events)
├── verification    (status, observed_context, expected_state, reason)
├── recovery        (failure_category, retry_count, fallback_used,
│                     recovery_strategy, replan_required)
├── reflection      (outcome, failure_category, lesson, confidence_delta,
│                     evolution_needed)
└── final           (success, partial, response, error)
```

### Why each field exists (brief)

- **task** — identity/lifecycle only. Replaces `active_tasks[task_id]` growing into a junk-drawer dict.
- **intent** — maps directly from the existing `IntentParser` output.
- **context** — a *snapshot* the graph reasoned over, not ownership of the context subsystem (`WindowDetector`, `StateExtractor`, `ContextValidator` stay external services).
- **knowledge** — new field; gives RAG/memory a clean home in state without owning the databases.
- **plan** — `steps` + `current_step_index` + `completed_steps`, replacing the empty `goal_stack.py`.
- **routing** — preserves the existing `MethodDecision` concept as state, so reflection/learning can see *how* a method was chosen.
- **safety** — records the *decision* (risk level, confirmation required, user decision) without replacing `PermissionGuard`/`SafetyValidator`/`ConfirmationManager`.
- **execution** — the *current* step's execution only (distinct from `history`, which is the full run).
- **history** — workflow-run history, distinct from persistent `memory/` — "what happened this run" vs. "what should be remembered after."
- **verification** — new; separates "executor said success" from "user's intended outcome actually happened" (e.g., a click succeeds but the UI never transitions).
- **recovery** — the workflow-level recovery decision (retry / re-route / replan), separate from the executor's low-level retry/fallback mechanics.
- **reflection** — maps directly onto the existing `Reflector`'s domain model.
- **final** — the terminal answer to "what does Operonix report to the user," distinct from `execution.result`.

### Explicitly excluded from state

`EventBus`, `Executor` instance, `ToolRegistry`, `PluginRegistry`, memory databases (ChromaDB/SQLite/JSON), the LLM client/model object, `WindowDetector`/`AppClassifier`. These are **services** — state holds their *results*, never the objects themselves.

### Field criticality

| Always present | Populated conditionally |
|---|---|
| task, intent, context, plan, execution, final | knowledge, routing, safety, verification, recovery, reflection |

### State ownership map

| State area | Primary producer | Consumers |
|---|---|---|
| task | graph intake | all nodes |
| intent | IntentParser / LangChain | planner, routing, safety |
| context | context subsystem | intent, planner, execution, verification |
| knowledge | RAG/memory services | planner/agent |
| plan | Planner / LangChain | safety, execution |
| routing | Routing Engine / agent | safety, executor |
| safety | Safety subsystem | graph routing |
| execution | Executor | verification, reflection |
| history | graph + executor | recovery, reflection |
| verification | context/automation | graph routing, reflection |
| recovery | graph + executor failure data | routing/planning |
| reflection | Reflector | learning, finalization |
| final | graph | API/UI/voice |

### What happens to `active_tasks`

It should **not** be deleted immediately. During migration it acts as a compatibility layer. Longer-term, `LangGraph`'s `OperonixState` becomes authoritative, and the orchestrator stops manually doing `task["context"] = ...`, `task["intent"] = ...`, etc.

---

## 4. Part B — Graph Topology

### 4.1 Baseline lifecycle

```
START
 ↓
INTAKE
 ↓
GATHER_CONTEXT (= OBSERVE, initial)
 ↓
ANALYZE_INTENT
 ↓
RETRIEVE_KNOWLEDGE
 ↓
CREATE_PLAN
 ↓
  for each plan step:
    ROUTE
     ↓
    SAFETY_CHECK
     ↓
    EXECUTE_STEP
     ↓
    OBSERVE (post-action)
     ↓
    VERIFY_STEP
     │
     ├── more steps  ──────────────► ROUTE (next step)
     ├── all complete ─────────────► REFLECT → FINALIZE
     └── failure ──────────────────► RECOVER
                                        ├── retry        → EXECUTE_STEP
                                        ├── context issue → GATHER_CONTEXT
                                        ├── routing issue → ROUTE
                                        └── plan issue    → CREATE_PLAN
```

SAFETY_CHECK outcomes branch three ways:

```
                     SAFETY_CHECK
                       │
        ┌──────────────┼──────────────┐
        ▼              ▼              ▼
     APPROVED       CONFIRM        BLOCKED
        │              │              │
        ▼              ▼              ▼
     EXECUTE      graph pauses,      FINALIZE
                  user decides,
                  graph resumes
```

### 4.2 Node inventory (12 nodes, not 20–30)

1. **intake** — deterministic; creates `state.task`. Replaces `orchestrator.handle_new_task()`. No LLM calls.
2. **gather_context / observe** — calls `WindowDetector`, `AppClassifier`, `StateExtractor`, `FocusTracker`, `ContextValidator`; writes `state.context`. Used for both *initial* understanding and *post-action* verification — context is never assumed static.
3. **analyze_intent** — first major LangChain integration point. `LangChain` does AI interpretation → existing `IntentParser`'s deterministic resolution/validation/keyword-fallback logic is preserved on top.
4. **retrieve_knowledge** — thin boundary over `VectorStore` / `SessionMemory` / `EpisodicMemory` / `LongTermMemory`; can be a no-op for tasks that don't need it.
5. **create_plan** — preserves the existing simple-task→deterministic-plan / complex-task→LLM-plan split. Graph doesn't care how the plan was produced.
6. **route** — see §4.3, the most significant revision.
7. **safety_check** — `PermissionGuard` + `SafetyValidator` + `ToolValidator` + `RiskRules` + `ConfirmationManager` + `Sandbox`, happens *after* routing (risk depends on the actual chosen operation).
8. **execute_step** — thin translation layer over the existing `Executor`. Executor logic (tool resolution, retry, fallback, focus, error classification, capability/plugin execution) is **not duplicated** in the node.
9. **verify_step** — new first-class stage; doesn't trust `ExecutionResult.success` alone. Compares expected vs. observed state.
10. **recover** — workflow-level recovery controller; routes failures to the *correct* upstream stage rather than a blind fallback chain.
11. **reflect** — thin wrapper over the existing `Reflector`; persistence (`LongTermMemory`, `EpisodicMemory`, `evolution_needed`) stays an event-driven subsystem outside the graph.
12. **finalize** — builds `final.success/partial/response/error` for API/Dashboard/Panel/Voice, so those interfaces never need to understand graph internals.

**Deliberately not graph nodes:** `CapabilityMapper`, `DecisionEngine`, `ToolSelector`. Their responsibilities are consolidated into the `route` node's internal pipeline (see below) instead of becoming five more boxes on the graph.

### 4.3 Routing revision — candidate ranking, not fixed priority

**Initial instinct (rejected on review):** keep `MethodRouter`'s fixed priority order `PLUGIN → API → SHELL → UI`.

**Problem:** these are execution *mechanisms*, not a ranking of *quality*. A plugin existing doesn't mean a plugin should win — e.g., an API might score higher on reliability/latency/reversibility for the same step. A hard 4-tier hierarchy also won't scale as Operonix adds browser automation, vision, computer-use models, MCP, remote services, etc.

**Revised model:**

```
Intent + Plan + Context
          │
          ▼
   Candidate Discovery  (Plugins, APIs, Shell, UI, future methods…)
          │
          ▼
   Candidate Evaluation (capability fit, context fit, reliability,
          │               learned success rate, availability, risk,
          │               permissions, latency, reversibility)
          ▼
   Policy / Safety Constraints (filter out non-viable candidates)
          │
          ▼
   Ranking
          │
          ▼
   MethodDecision → Executor
```

**What to keep from the current `MethodRouter`** (it's a genuinely strong implementation):
- A single, centralized routing authority (don't let intent parser / tool selector / executor each pick methods independently).
- Rejection-reason recording (`PLUGIN rejected: confidence below threshold`, etc.) — expand this into a full evidence table (candidate, score, decision, reason) for dashboard/learning visibility.
- The immutable `MethodDecision` object, fallback chain, and pre-serialized payloads — evolve, don't discard:

```
MethodDecision
├── selected_candidate
├── confidence
├── candidates_considered
├── rejected_candidates
├── fallback_candidates
├── policy_decision
├── safety_constraints
└── routing_explanation
```

**What changes:** `MethodType` (PLUGIN/API/SHELL/UI/...) becomes a set of *execution categories* to rank, not a hierarchy to descend. A deployment can still express a **policy-level preference** (e.g., "prefer deterministic native capability, then trusted plugin, then API, then shell, then UI") — but that's configuration, not architecture, and specific situations (destructive tasks, tasks needing visual state, tasks needing exact browser behavior) can override it.

This also unlocks better recovery: instead of blindly descending the priority list on failure, recovery can ask *why* the candidate failed (`ENV_TRANSIENT` → retry same candidate; `ROUTING_MISMATCH` → re-rank; `ENV_PERMANENT` → remove candidate, re-rank) — and it gives the `learning/` subsystem a natural place to feed in historical success rates per (intent, context, method).

### 4.4 Safety revision — constraint *and* gate, not just a gate

Safety participates twice:

```
ROUTE
 ↓
SAFETY CONSTRAINTS   (filters candidates before ranking)
 ↓
RANK
 ↓
MethodDecision
 ↓
SAFETY FINAL CHECK   (authorizes immediately before execution)
 ↓
EXECUTE
```

This lets the system re-rank around a rejected candidate (e.g., plugin lacks permission → fall back to an allowed method) instead of dead-ending the task.

### 4.5 Recovery revision — route to the right stage, not a universal fallback

```
                   VERIFY
                     │
                   failure
                     ▼
                  RECOVER
                     │
        ┌────────────┼────────────┐
        ▼            ▼            ▼
   context issue  routing issue  plan issue
        │            │            │
        ▼            ▼            ▼
     OBSERVE       ROUTE         PLAN
```

E.g., a disappeared UI element → re-observe context, not a full replan. An unavailable API → re-route, not an LLM re-derivation of the whole task. A wrong planning assumption → replan.

### 4.6 Planning vs. routing boundary (clarified)

- **Planner answers:** *what* needs to be accomplished (goal-oriented steps).
- **Router answers:** *how* each step should be accomplished given the current environment.

Routing therefore happens **per step**, not once for the whole task:

```
PLAN
 ↓
for each step:
    ROUTE → SAFETY → EXECUTE → OBSERVE → VERIFY → next step
```

### 4.7 Where "the agent" lives

Operonix as a whole is the agent — not LangGraph alone, not LangChain alone:

```
                 OPERONIX AGENT
                       │
             ┌─────────┴─────────┐
             ▼                   ▼
        LangGraph             LangChain
        Workflow              Intelligence
             │                   │
             └─────────┬─────────┘
                       ▼
              Operonix services
```

Safety must always sit **outside** the AI layer: `LLM → LangGraph → Routing → Safety/Policy → Executor` — never `LLM → LangGraph → Executor` directly.

---

## 5. Part C — Module-by-Module Migration Map

**Ownership rules used to judge every module:**

| Layer | Owns |
|---|---|
| CORE | application/runtime infrastructure |
| GRAPH | task workflow, state transitions |
| AI | LLM reasoning, structured output, RAG, AI-facing tools |
| ROUTING | candidate discovery/evaluation/ranking |
| SAFETY | authorization, risk, permissions, policy |
| EXECUTOR | reliable action execution |
| CAPABILITIES/PLUGINS | actual operations |
| CONTEXT/AUTOMATION | observation of machine/UI |
| MEMORY/LEARNING | persistent experience, learned behavior |
| EVENT BUS | system-wide async communication |

### 5.1 `core/`

| Module | Decision | Notes |
|---|---|---|
| `orchestrator.py` | **REPLACE GRADUALLY** | Becomes an Operonix Runtime Host: startup, shutdown, service wiring, event-bus integration, graph invocation, external interfaces — not per-task lifecycle logic. |
| `event_bus.py` | **KEEP** | System events, plugin events, dashboard, lifecycle, logging, learning/evolution events. |
| `lifecycle_manager.py` | **KEEP** | Pure runtime infra. |
| `config.py` | **KEEP** | Add graph/AI configuration here rather than scattering it into nodes. |
| `logger.py` | **KEEP** | — |
| `error_handler.py` | **KEEP / MODIFY** | Infra-level error handling; graph-level errors additionally become `state.execution.error` / `state.recovery.failure_category`. |
| `watchdog.py` | **KEEP** | Runtime reliability, not an AI concern. |
| `metrics.py` | **KEEP / MODIFY** | Eventually collect node latency, replans, retries, routing failures, tool failures, verification failures, completion rate. |
| `mode_manager.py` | **KEEP / MODIFY** | May influence graph policy but isn't graph logic. |
| `input_mode.py` | **KEEP** | — |
| `terminal_resolver.py` | **KEEP** | Deterministic environment infra. |
| `config_validator.py` | **KEEP** | — |
| `main.py` | **MODIFY** | Boots services + graph runtime + event bus + API/UI rather than the old orchestration path. |

### 5.2 `brain/`

| Module | Decision | Notes |
|---|---|---|
| `llm_client.py` | **REPLACE** | Becomes `ai/models/` on LangChain, preserving today's provider-independent interface (Ollama, Groq, Gemini, OpenRouter) behind an `Operonix ModelService`. Nothing else should depend on LangChain model objects directly. |
| `intent_parser.py` | **MODIFY → eventually MERGE into AI layer** | Keep deterministic resolution, parameter normalization, manifest resolution, fallback, risk hints. Move LLM interpretation to LangChain. |
| `capability_mapper.py` | **MERGE / PARTIALLY RETIRE** | Semantic capability-discovery logic feeds the new Routing Engine; overlapping duties with DecisionEngine/ToolSelector/MethodRouter/PluginRegistry are consolidated. |
| `decision_engine.py` | **RETIRE / MERGE** | Largest source of duplicated decision logic. Workflow decisions → LangGraph; execution-method decisions → Routing Engine; task priority/concurrency → runtime infra. No standalone AI DecisionEngine needed once separated. |
| `goal_stack.py` | **RETIRE** | Empty in current codebase; use `state.plan.*` instead. |
| `intent_matcher.py` | **MERGE** | Useful matching logic folds into intent resolution / capability discovery / routing as appropriate. |
| `planner.py` | **MODIFY → AI planning service** | Preserve simple→deterministic / complex→AI split; complex reasoning moves to LangChain. |
| `reflector.py` | **KEEP / MODIFY** | Becomes the graph's `reflect` node/service; persistent learning stays outside the graph. |
| `validator_llm.py` | **MODIFY / NARROW** | LLM-assisted semantic validation only; deterministic safety system remains authoritative. |

### 5.3 `tools/`

| Module | Decision | Notes |
|---|---|---|
| `base_tool.py` | **KEEP** | Internal tool contract. `LangChain Tool → Operonix Tool Adapter → BaseTool`. |
| `tool_registry.py` | **KEEP / MODIFY** | Canonical registry; not replaced by LangChain's. |
| `tool_selector.py` | **MERGE** | Overlaps with new Routing Engine's candidate discovery/scoring/fallback. |
| `method_router.py` | **MAJOR MODIFY** | Keep centralized authority; change fixed priority into candidate-based ranking (§4.3). |
| `routing_decision.py` | **KEEP / EVOLVE** | Strong existing contract; expand `MethodDecision` fields rather than replace. |
| `tool_validator.py` | **MERGE WITH SAFETY BOUNDARY / KEEP CORE** | `AI proposes → Tool validation → Safety → Executor`. |
| `payload_serializers.py` | **KEEP** | Deterministic translation — shouldn't become LLM logic. |
| `process_bridge.py` | **KEEP** | Infra. |
| `file_tool.py` / `shell_tool.py` / `ui_tool.py` / `api_tool.py` | **KEEP / ADAPT** | Expose through the new AI-facing tool adapter; `shell_tool.py` especially keeps a strong safety boundary — never an unrestrained LLM shell agent. |
| `ollama_tool.py` | **MODIFY / SPECIALIZE** | Decide if it stays a model-facing helper or folds into the LangChain model layer. |
| `ollama_executor.py` | **REVIEW / LIKELY MERGE** | Overlaps model/tool execution infra; needs a unique responsibility to justify staying separate. |
| `smart_file_patcher.py` | **KEEP** | Specialized deterministic capability. |

### 5.4 `executor/`

| Module | Decision | Notes |
|---|---|---|
| `executor.py` | **KEEP** | Graph calls it: `EXECUTE_STEP → Executor → ExecutionResult`. |
| `retry_manager.py` | **KEEP / MODIFY** | Low-level retry mechanics; graph decides *whether* to retry, this decides *how*. |
| `fallback_manager.py` | **KEEP / MODIFY** | Same split as retry. |
| `error_classifier.py` | **KEEP / MODIFY** | Feeds `state.recovery.failure_category` and graph routing directly. |
| `execution_tracker.py` | **KEEP** | Feeds execution history/observability. |
| `focus_manager.py` | **KEEP** | Pure execution/environment infra. |

### 5.5 `safety/` — all **KEEP**

`validator.py`, `permission_guard.py`, `risk_rules.py`, `confirmation.py` (integrate with graph pause/resume later), `sandbox.py`, `audit.py` (expand to log graph transitions too). This is the hard policy boundary — no rewrites here.

### 5.6 `context/` — all **KEEP**

`window_detector.py`, `app_classifier.py`, `app_profiler.py`, `context_validator.py`, `focus_tracker.py`, `permission_checker.py`, `state_extractor.py`. Called through `OBSERVE`/`GATHER_CONTEXT`; not individually promoted to graph nodes.

### 5.7 `automation/` — all **KEEP**

`screen_reader`, `selector_engine`, `vision_model`, `ui_fallback`. Execution/observation mechanisms — LangChain doesn't replace them; LangGraph orchestrates when they're used.

### 5.8 `capabilities/` — all **KEEP**

`registry.py`, `file_ops.py`, `text_ops.py`, `command_ops.py`, `ui_ops.py`, `web_ops.py`, `validation_rules.py`. `bootstrap.py` **KEEP/MODIFY** — the only change is wrapping capabilities in the Operonix Tool Adapter for LangChain visibility, not rewriting them.

### 5.9 `plugins/` — nearly all **KEEP**

`registry.py`, `loader.py`, `manifest_schema.py`, `plugin_validator.py`, `sandbox_runner.py`, `plugin_health_monitor.py`, `plugin_rollback.py`, `plugin_memory.py`, `template_engine.py` — **KEEP**. `capability_gap_detector.py` **KEEP/MODIFY**; `generator.py`, `plugin_evolver.py` **KEEP/LATER**. Future flow: `Plugin → registered capability → candidate → routing → safety → executor`.

### 5.10 `memory/` — all **KEEP** (or KEEP/MODIFY)

`session_memory.py`, `episodic.py`, `long_term_memory.py` — **KEEP**; `vector_store.py` **KEEP/MODIFY** to become graph-accessible services (never graph state itself).

### 5.11 `learning/`

| Module | Decision |
|---|---|
| `learner.py` | **KEEP / MODIFY** — feeds the new candidate-ranking router with historical performance signals (through routing policy, never a direct mutation of routing decisions). |
| `retriever.py` | **KEEP** — good future RAG integration point. |
| `pattern_validator.py`, `pruning.py`, `prompt_trust.py` | **KEEP / MODIFY** — stay outside the core graph. |

### 5.12 `debugging/` — all **KEEP** (some LATER)

`error_listener.py`, `error_parser.py`, `fix_validator.py`, `rollback_manager.py` — **KEEP**; `auto_fix.py` **KEEP/LATER**. Important distinction: debugging ≠ workflow recovery. The graph decides "we need recovery"; debugging can propose "this looks like an implementation/config problem, here's a possible repair."

### 5.13 `voice/`, `panel/` — **KEEP**

Pure input/output subsystems; the graph is unaware of voice/panel internals, and vice versa.

### 5.14 `api/` — **KEEP / MODIFY**

Becomes a gateway to the task runtime: `POST /task → Operonix Runtime → LangGraph`, rather than directly coordinating IntentParser/Planner/Executor.

### 5.15 `dashboard/` — **KEEP / MODIFY**

Becomes graph-aware: can show current node, state, plan, selected tool, safety decision, execution, verification, recovery per task — a real observability upgrade over today's loosely related event stream.

### 5.16 High-level rollup

```
KEEP                          MODIFY                         MERGE                          RETIRE / REPLACE
─────────────────────────     ─────────────────────────     ─────────────────────────     ─────────────────────────
Executor                      Orchestrator                  GoalStack                     old AI orchestration flow
Capabilities                  IntentParser                  IntentMatcher                  old LLM provider impl
Automation                    CapabilityMapper               DecisionEngine                 duplicated routing paths
Context                       Planner                       ToolSelector routing logic     manual task-state
Safety                        Reflector                     some CapabilityMapper logic       orchestration
Plugins                       LLMClient → LangChain adapter some Retry/Fallback graph
Memory                        MethodRouter                    decisions
Learning                      ToolRegistry                  some validation layers
Voice                         ToolSelector
Panel                         Validator LLM
API                           Learning/RAG interfaces
EventBus                      API/dashboard integration
Lifecycle / Infra
```

**Single biggest consolidation:**

```
CURRENT                                          FUTURE
IntentParser → CapabilityMapper →                        LANGGRAPH
DecisionEngine → ToolSelector →              ┌──────────┴──────────┐
MethodRouter → Executor                      INTENT                PLAN
                                                    └──────────┬──────────┘
                                                          ROUTING ENGINE
                                                     ┌─────────┼─────────┐
                                                  Plugin      API      Shell/UI…
                                                     └─────────┼─────────┘
                                                             SAFETY
                                                                ↓
                                                            EXECUTOR
```

Rule: **don't preserve five independent places that can each decide "what tool should we use?" — there must be one authoritative routing architecture.**

---

## 6. Part D — Interfaces & Contracts

**Guiding principle:** *State crosses the graph boundary; services cross the service boundary; events cross the system boundary.*

```
┌──────────────────────────────────────────────────────────┐
│  TASK WORKFLOW — LangGraph State                          │
│  nodes read/write workflow-relevant data                  │
└─────────────────────────┬────────────────────────────────┘
                          │ service calls
                          ▼
┌──────────────────────────────────────────────────────────┐
│  SERVICES — Context, Routing, Safety, Executor,            │
│  Memory, Plugins (return typed domain results)             │
└─────────────────────────┬────────────────────────────────┘
                          │ events
                          ▼
┌──────────────────────────────────────────────────────────┐
│  SYSTEM INTEGRATION — EventBus →                            │
│  Dashboard / API / Learning / Logs / Voice                 │
└──────────────────────────────────────────────────────────┘
```

### 6.1 Runtime ↔ Graph

The runtime creates initial state and invokes the graph; it does **not** decide intent, plan, routing, retry, or replanning — those belong to the workflow.

### 6.2 The most important structural change: nodes call services directly

**Today**, internal task steps are wired through EventBus (`IntentParser → event → CapabilityMapper → event → Planner → event → Safety`), which is why the orchestrator needs bookkeeping fields like `_pending_safe_event`, `_pending_safe_source`, `_dispatched_safe`, `_high_risk_pending`.

**Going forward**, EventBus stops being the primary transport *within* one workflow:

```
LangGraph
   ├── analyze_intent()  → IntentService
   ├── create_plan()     → PlannerService
   ├── route()           → RoutingEngine
   ├── safety_check()    → SafetyService
   └── execute()         → Executor
```

EventBus doesn't disappear — it stops being the mechanism that reconstructs one task's state across async hops.

### 6.3 Graph ↔ LangChain

Narrow boundary: LangGraph calls LangChain for AI operations only (`analyze_intent`, `create_plan`, `retrieve_knowledge`) and always converts the result into a domain object before storing it in state. **LangChain never owns Operonix state.**

### 6.4 Key typed contracts to introduce

```
TaskRequest, IntentResult, ContextSnapshot, KnowledgeContext,
Plan, PlanStep, RoutingCandidate, MethodDecision, SafetyDecision,
ExecutionRequest, ExecutionResult, VerificationResult,
RecoveryDecision, ReflectionResult, FinalResult
```

Two contracts deserve special attention:

**`PlanStep`** — the shared semantic object connecting planning, routing, safety, execution, and verification:
```
PlanStep
├── step_id, action, arguments
├── objective, expected_outcome
├── dependencies
└── metadata
```

**`MethodDecision`** (evolved) — candidate-based, not priority-based:
```
MethodDecision
├── selected (method, tool, confidence)
├── candidates (all evaluated)
├── fallbacks
├── payloads
├── constraints
└── evidence
```

### 6.5 Confirmation flow (pause/resume, not event-continuation)

Current architecture threads confirmation through `confirmation_required` → `ConfirmationManager` → dashboard → `user_response_received` → `task_safety_cleared` → orchestrator → executor. This works but adds a lot of coordination state.

**Future:**
```
SAFETY_CHECK → CONFIRMATION_REQUIRED → GRAPH PAUSES
                                            │
                              user decision (via dashboard/API)
                                            │
                                       RESUME GRAPH
                                       ┌────┴────┐
                                     allow       deny
                                       │           │
                                   EXECUTE     FINALIZE
```

### 6.6 Five non-negotiable rules

1. Graph state is the canonical state of one running task.
2. Services return domain results; they never mutate graph state directly.
3. EventBus is for system-wide communication, not primary task sequencing.
4. LLM output is always converted into a constrained domain object before reaching execution.
5. The Executor never decides workflow strategy, and the graph never performs low-level execution.

### 6.7 Full interface diagram

```
                         USER
                          │
                          ▼
                    INPUT ADAPTERS  (Voice / Panel / API)
                          │
                          ▼
                 OPERONIX RUNTIME
                          │
                          ▼
                    LANGGRAPH  (OperonixState)
                          │
       ┌─────────────────┼─────────────────┐
       ▼                 ▼                 ▼
   LangChain         Context           Memory/RAG
   (AI results)   (ContextSnapshot)   (Knowledge)
       │                 │                 │
       └─────────────────┼─────────────────┘
                         ▼
                       PLAN → ROUTING ENGINE → MethodDecision
                         │
                       SAFETY → SafetyDecision
                         │
                     EXECUTOR → ExecutionResult
                         │
                      OBSERVE → VERIFY
                         │
              ┌──────────┴─────────┐
           success                failure
              │                    │
           REFLECT              RECOVER (retry / route / replan)
              │                    │
          FINALIZE ◄───────────────┘

Around all of this:  EVENT BUS → Dashboard / Logs / Metrics / Learning / Plugins
```

---

## 7. Migration Sequence

| Phase | Scope | Behavior change? |
|---|---|---|
| **M1** | Create `graph/state.py`, `graph/graph.py`, `graph/nodes/` skeleton | None |
| **M2** | Introduce `ai/models/`; LangChain sits under an Operonix model interface | None (adapter only) |
| **M3** | Graph intake + context: replace `active_tasks` + context-snapshot events with explicit state for the graph-managed path | Minimal |
| **M4** | Graph `analyze_intent` node calling existing `IntentParser`; gradually replace its LLM calls with LangChain structured output | Incremental |
| **M5** | Graph `create_plan` node calling existing `Planner`; later replace complex-plan branch with LangChain structured planning | Incremental |
| **M6** | Graph `safety_check` node integrating `PermissionGuard`/`SafetyValidator`/`ConfirmationManager` without weakening them | None functionally, structural only |
| **M7** | Graph `execute_step` node calling `Executor` through an explicit interface | None |
| **M8** | Graph `verify_step` + `recover` — this is where LangGraph starts paying off (execute → verify → success/retry/recover/replan) | New capability |
| **M9** | Tool adapters: expose `BaseTool`, `ToolRegistry`, plugins, capabilities to LangChain | New capability |
| **M10** | RAG + memory integration (`retrieve_knowledge` node) — only after the basic graph is stable | New capability |
| **M11** | Retire/merge `DecisionEngine`, old event-driven planner routing, duplicated tool routing — **only after the graph is proven** | Cleanup |

**First vertical slice** (recommended): *"Open Firefox and search for autonomous agents"* — already the documented canonical workflow. Implement only this path first:

```
START → intake → context → intent → plan → safety → execute → verify
          │                                                      │
          └─────────────────────── success ────────► reflect → finalize
                                     │
                                  failure ──────────► recover
```

with each node delegating to the *existing* `IntentParser`, `Planner`, `SafetyValidator`/`PermissionGuard`, `Executor`, and `Reflector` — the graph is the conductor, the existing components remain the orchestra. Do **not** create `graph/intent_parser.py`, `graph/planner.py`, etc. as copies of existing modules — nodes are thin adapters, not duplicate implementations.

---

## 8. Architectural Principles Recap

1. EventBus = system-wide communication. LangGraph = one task's workflow. LangChain = AI capability layer. Executor = execution engine. Safety = hard policy boundary. Memory = persistent knowledge/experience.
2. Routing is candidate-based (discover → evaluate → constrain → rank), never a hard-coded priority list.
3. Safety acts both as a *constraint* on candidate routing and as a *final gate* before execution.
4. Recovery routes failures back to the specific upstream stage responsible (context / routing / plan), not a single generic fallback.
5. Context/observation is treated as dynamic — there's an explicit "initial observation" and "post-action observation," never one static snapshot reused for the whole task.
6. Planner decides *what*; Router decides *how*, per step.
7. The "agent" is all of Operonix — LangGraph and LangChain are internal components of it, not the whole system.
8. No graph-internal duplication of existing modules; nodes are thin adapters over existing services during migration, with internals migrated gradually afterward.

---

## 9. State Persistence & Checkpointing

### 9.1 Three different kinds of persisted information

Operonix must distinguish between **workflow state**, **workflow checkpoints**, and **persistent memory**. These are related but are not the same thing.

```
GRAPH STATE
    = current in-memory state of one running workflow

GRAPH CHECKPOINT
    = persisted snapshot of a workflow that can be resumed

MEMORY
    = persistent knowledge / experience that may be reused across tasks
```

```
                 CURRENT TASK
                      │
              ┌───────┴────────┐
              ▼                ▼
         Graph State       Checkpoint
              │                │
              │          survives interruption
              │                │
              └────────┬───────┘
                       ▼
                  Workflow
                   resumes

                       +

                   MEMORY
                       │
                       ▼
             survives task completion
             and may influence future tasks
```

### 9.2 Why checkpointing is required

Necessary for: human confirmation pauses, long-running workflows, process restarts, recoverable failures, temporary disconnection in hybrid deployments, crash recovery, and future distributed execution. A graph must not depend on the process remaining alive continuously.

### 9.3 Checkpoint contents

Should contain enough to resume the workflow safely:

```
task identity
workflow state
current graph/node position
current plan step
completed steps
routing decision
safety/confirmation state
execution status
retry/recovery information
relevant context snapshot
state version
checkpoint timestamp
```

Should **not** contain service instances, open handles, live Python objects, or external resources that can't be reconstructed safely.

### 9.4 State versioning

```
state_schema_version
workflow_version
```

must let the runtime detect incompatible state before attempting to resume an old workflow.

### 9.5 Resume semantics

A resumed workflow must not blindly repeat an action that may already have occurred:

```
checkpoint
    ↓
restore state
    ↓
inspect current environment
    ↓
determine whether current step already happened
    ↓
continue / verify / retry / replan
```

This is tightly coupled to the idempotency and verification architecture (§11).

---

## 10. Cancellation, Timeouts & Abort Semantics

Cancellation and timeout are first-class workflow outcomes, not ordinary execution failures.

### 10.1 Task lifecycle

```
PENDING
RUNNING
PAUSED
WAITING_FOR_CONFIRMATION
RECOVERING
COMPLETED
PARTIAL
FAILED
CANCELLED
ABORTED
TIMED_OUT
```

### 10.2 Cancellation ownership

```
User / API / Dashboard / Runtime
            │
            ▼
      cancellation request
            │
            ▼
         LangGraph
            │
            ▼
     stop workflow progression
            │
            ▼
      Executor cancellation
```

The graph owns **workflow cancellation**; the executor owns the mechanics required to stop or safely unwind an active operation where possible.

### 10.3 Cancellation must not equal failure

```
CANCELLED ≠ FAILED
```

Reflection and learning should record the distinction.

### 10.4 Timeout layers

```
Operation timeout → Executor
Step timeout      → Graph / execution policy
Task timeout      → Graph / Runtime
System timeout    → Runtime / watchdog
```

A timeout should produce a structured classification rather than an untyped exception:

```
TIMEOUT
├── operation_timeout
├── step_timeout
├── task_timeout
└── system_timeout
```

### 10.5 Safe abort

For side-effecting operations, cancellation must consider whether the current action can safely stop:

```
interruptible
non_interruptible
requires_cleanup
requires_verification_after_abort
```

The executor is responsible for action-level cleanup; the graph determines what happens to the workflow afterward.

---

## 11. Idempotency, Side Effects & Re-execution Safety

Retries and graph resumption make idempotency a core architectural concern.

### 11.1 Why this matters

An operation can succeed even when its result is lost:

```
Executor performs action
        ↓
action succeeds
        ↓
response lost / process interrupted
        ↓
graph resumes
        ↓
same action appears unfinished
```

Blindly repeating it may produce duplicate or destructive effects.

### 11.2 Extended `PlanStep` execution semantics

```
PlanStep
├── action
├── arguments
├── objective
├── expected_outcome
├── dependencies
├── preconditions
├── postconditions
├── risk_hint
├── retry_policy
├── timeout
├── idempotency
└── reversibility
```

Idempotency classification:

```
SAFE           — repeated execution is normally harmless
CONDITIONAL    — repeated execution requires verification first
NON_IDEMPOTENT — repeated execution may cause additional side effects
```

### 11.3 Side-effect classification

```
READ_ONLY
REVERSIBLE
LIMITED_SIDE_EFFECT
DESTRUCTIVE
EXTERNAL_COMMIT
```

This can influence routing, safety, retry, confirmation, recovery, and verification.

### 11.4 Recovery rule

The system must never treat "execution failed" as sufficient evidence that "the action did not happen." Before retrying a potentially side-effecting action:

```
execution failure
      ↓
observe environment
      ↓
check expected postcondition
      ↓
already completed?
   /          \
 yes          no
 │             │
verify        retry/recover
```

This should be treated as a fundamental reliability rule.

---

## 12. Error Taxonomy & Recovery Semantics

Recovery requires a stable error taxonomy shared by the executor, graph, safety system, and observability layer.

### 12.1 Top-level error categories

```
TaskError
│
├── InputError
├── IntentError
├── ContextError
├── KnowledgeError
├── PlanningError
├── RoutingError
├── PermissionError
├── SafetyError
├── ConfirmationError
├── ExecutionError
├── VerificationError
├── TimeoutError
├── CancellationError
└── SystemError
```

### 12.2 Execution failure subclasses

```
ExecutionError
├── TRANSIENT
├── PERMANENT
├── ENVIRONMENTAL
├── CONTEXT_MISMATCH
├── ROUTING_MISMATCH
├── TOOL_UNAVAILABLE
├── PERMISSION_DENIED
├── VALIDATION_REJECTED
└── UNKNOWN
```

These are example semantic classifications, not necessarily final enum names.

### 12.3 Recovery mapping

```
Failure
   │
   ├── TRANSIENT              → retry
   ├── CONTEXT_MISMATCH       → observe
   ├── ROUTING_MISMATCH       → re-route
   ├── PLANNING_ERROR         → re-plan
   ├── PERMISSION / SAFETY    → block / confirmation / finalize
   ├── TOOL_UNAVAILABLE       → re-route
   ├── VERIFICATION_FAILURE   → observe → recover
   └── UNKNOWN / SYSTEM       → controlled failure
```

### 12.4 Error ownership

```
Executor = classify execution-level failures
Safety   = classify authorization/policy failures
Context  = classify observation/context failures
Graph    = decide workflow response to classified failures
Runtime  = handle system/process-level failures
```

No single layer should own every category.

---

## 13. Observability & Execution Tracing

Observability is a first-class architectural requirement for Operonix. The system should eventually be able to answer:

```
What did the user ask?
What did Operonix infer?
What context did it observe?
What knowledge did it retrieve?
What plan did it generate?
What execution candidates were considered?
Why was one selected?
What safety checks occurred?
What tool was executed?
What happened during execution?
What did verification observe?
Why did recovery happen?
How many retries occurred?
What did reflection conclude?
```

### 13.1 Execution trace

```
Task
 ├── Intake
 ├── Observation
 ├── Intent
 ├── Knowledge retrieval
 ├── Plan
 ├── Routing decisions
 ├── Safety decisions
 ├── Execution attempts
 ├── Observations
 ├── Verification
 ├── Recovery
 ├── Reflection
 └── Final outcome
```

### 13.2 Trace events

EventBus remains appropriate for publishing observable events such as:

```
node_started
node_completed
routing_candidate_evaluated
routing_decision_made
safety_checked
confirmation_requested
execution_started
execution_completed
verification_completed
recovery_started
task_paused
task_resumed
task_cancelled
task_completed
task_failed
```

These events are for observability/integration, not for reconstructing the workflow's canonical state.

### 13.3 Routing evidence

Routing should expose:

```
candidate
score
decision
rejection reason
policy constraints
context compatibility
historical performance
fallback position
```

This makes routing explainable to both developers and the learning subsystem.

### 13.4 State snapshots vs. event logs

```
Checkpoint        = "Where is the workflow now?"
Execution Trace   = "What happened?"
Audit Log         = "What security/safety decisions occurred?"
Memory            = "What should be remembered for future tasks?"
```

---

## 14. Human-in-the-Loop Architecture

Human intervention should be a general workflow capability, with confirmation as the first use case.

### 14.1 Human intervention types

```
CONFIRM
DENY
CLARIFY
CHOOSE
PROVIDE_INFORMATION
TAKE_OVER
ABORT
```

### 14.2 General interaction model

```
Graph
  ↓
Human intervention required
  ↓
Persist checkpoint
  ↓
PAUSE
  ↓
External interface
  ↓
Human response
  ↓
Validate response
  ↓
Resume graph
```

### 14.3 Confirmation is only one case

The existing confirmation flow should evolve into a general intervention mechanism rather than a separate architecture per interaction type:

```
Ambiguous request        → ASK_USER
High-risk action         → CONFIRM
Missing information      → REQUEST_INPUT
Difficult automation state → REQUEST_TAKEOVER
```

### 14.4 Human decisions must be recorded

```
intervention type
reason
request timestamp
user response
response timestamp
decision source
```

Useful for auditing, debugging, and future learning.

---

## 15. Concurrency & Resource Ownership

Operonix is not a single-threaded conceptual system — multiple tasks, background operations, UI observations, and async services may coexist. Concurrency needs explicit ownership rules.

### 15.1 Graph instances

Each active task should have isolated workflow state:

```
Task A → Graph State A
Task B → Graph State B
Task C → Graph State C
```

No task should mutate another task's workflow state.

### 15.2 Shared resources

Not safely concurrent:

```
desktop focus
keyboard
mouse
current application
current window
certain filesystem operations
plugin sandboxes
system-level commands
```

These need resource ownership or serialization.

### 15.3 Resource model

```
Task
  ↓
requests resource
  ↓
Resource Manager / lock
  ↓
acquire
  ↓
execute
  ↓
release
```

The exact locking mechanism remains an implementation decision.

### 15.4 Graph concurrency vs. execution concurrency

```
Logical workflow concurrency ≠ Physical desktop concurrency
```

Two graph instances may exist simultaneously while only one may safely control the active desktop at a time.

### 15.5 Focus and UI ownership

`FocusManager`, window detection, and UI automation remain responsible for low-level focus/resource handling. The graph receives resulting state and decisions — it doesn't manipulate locks directly.

---

## 16. Migration Testing & Verification Strategy

The migration must be validated continuously against existing Operonix behavior.

### 16.1 Unit tests

Test services independently: `IntentParser`, `Planner`, `RoutingEngine`, `Safety`, `Executor`, `Verification`, `Reflector`, tool adapters.

### 16.2 Contract tests

Every new boundary needs contract tests:

```
Graph → Planner
Graph → RoutingEngine
Graph → Safety
Graph → Executor
LangChain → Tool Adapter
Tool Adapter → BaseTool
Plugin → Capability Registry
```

These verify one side can depend on the other without relying on undocumented payload behavior.

### 16.3 Graph-node tests

```
given state
    ↓
node
    ↓
expected state delta
```

### 16.4 Routing tests

Test candidate selection under changing conditions: capability available/unavailable, permission granted/denied, context changed, candidate reliability changed, plugin unhealthy, API unavailable, learned ranking changed. Goal: prove candidate ranking doesn't collapse into hidden fixed priorities.

### 16.5 Safety tests

Allowed actions, blocked actions, confirmation-required actions, permission failures, policy violations, malformed tool calls, unsafe parameters.

### 16.6 Recovery tests

Retry, re-route, re-observe, re-plan, abort, timeout, cancellation, verification failure, partial completion.

### 16.7 Regression testing

The existing system is the behavioral baseline:

```
same request
   ├── legacy path
   └── new graph path
          │
          ▼
       compare
```

Compare: success/failure, selected method, execution result, safety decision, side effects, final response. Exact internal sequences don't need to match, but externally meaningful behavior must remain correct.

### 16.8 Shadow / comparison mode

During migration, the new graph should support a controlled comparison mode where its decisions can be evaluated against the legacy system without replacing production execution.

---

## 17. Migration Rollback & Feature Control

The migration must be reversible until the new architecture is proven.

### 17.1 Legacy and graph paths

```
                Operonix Runtime
                       │
                 feature flag
                  /          \
                 ▼            ▼
          Legacy Workflow   LangGraph
```

The legacy path stays available until the corresponding graph path passes acceptance criteria.

### 17.2 Feature flags

```
USE_LANGGRAPH
USE_LANGCHAIN_MODELS
USE_GRAPH_ROUTING
USE_GRAPH_EXECUTION
USE_GRAPH_CONFIRMATION
```

Exact flag names are implementation details.

### 17.3 Rollback requirements

Every major migration milestone needs: a rollback path, a known-good commit/version, a configuration switch, test evidence, and data migration considerations.

### 17.4 Do not perform irreversible cleanup too early

Retire an old module only after: new implementation works, tests pass, regression passes, observability is sufficient, and rollback is understood.

---

## 18. Dependency & Configuration Migration

### 18.1 Dependency ownership

```
AI dependencies
    ├── LangChain
    ├── LangGraph
    └── model integrations

Core infrastructure
    ├── FastAPI
    ├── WebSocket
    └── system dependencies
```

Avoid unnecessarily coupling low-level capabilities to LangChain.

### 18.2 Model/provider configuration

Existing provider flexibility must remain: Ollama, Groq, Gemini, OpenRouter — via a provider-independent model configuration layer.

### 18.3 Configuration rule

Business/configuration decisions stay in Operonix configuration, not hard-coded inside graph nodes or prompts.

---

## 19. Repository Transition Strategy

Distinguish between **current location**, **transitional location**, and **final location** rather than performing one large repository reorganization.

### 19.1 Transitional principle

```
graph/nodes/intent.py
        ↓
existing IntentParser
```

New graph nodes are thin adapters initially, not duplicate implementations.

### 19.2 Finalization principle

Physically reorganize responsibilities only after: ownership is proven, interfaces are stable, tests cover the boundary, and legacy code is no longer required. Repository structure should follow stable responsibility boundaries, not lead the architectural migration.

---

## 20. Acceptance Criteria & Definition of Done

The migration isn't complete merely because LangChain/LangGraph appears in dependencies.

### 20.1 Architecture acceptance

```
LangGraph owns task workflow
LangChain owns AI primitives
Routing owns execution-method selection
Safety owns authorization/policy
Executor owns reliable execution
Capabilities/Plugins own operations
Context owns observation
Memory/Learning own persistence and learning
EventBus owns system-level communication
```

### 20.2 Functional acceptance

Representative tasks must demonstrate: intent understanding, planning, per-step routing, safety validation, execution, observation, verification, retry, fallback/re-routing, replanning, reflection, final response.

### 20.3 Reliability acceptance

Must correctly handle: transient failure, context change, tool unavailable, routing mismatch, verification failure, timeout, cancellation, partial completion, process interruption, workflow resume.

### 20.4 Safety acceptance

Must prove: LLM cannot bypass safety, LLM cannot directly bypass executor controls, unsafe tools are rejected, permissions remain enforced, confirmation remains functional, audit logging remains intact.

### 20.5 Regression acceptance

Existing supported behavior should not regress without an explicitly accepted design change.

### 20.6 Observability acceptance

Developers should be able to determine: what happened, why it happened, what was selected, why alternatives were rejected, what failed, how recovery was chosen, what the final outcome was.

### 20.7 Migration completion criterion

A module may be retired only when: replacement is operational, replacement has test coverage, behavior is verified, dependencies are migrated, observability exists, rollback is understood, and no required caller depends on the legacy contract.

---

## 21. Cross-Cutting Architectural Invariants

**Invariant 1 — Workflow state has one owner.** One running task → one canonical graph state.

**Invariant 2 — Services remain independently usable.** A service shouldn't require LangGraph merely to perform its basic responsibility.

**Invariant 3 — AI never receives unrestricted execution authority.** AI output must cross `domain contract → routing → safety → executor` before privileged actions occur.

**Invariant 4 — Execution does not determine workflow.** The executor reports what happened; the graph decides what happens next.

**Invariant 5 — Registries describe capabilities.** Registries answer "what exists?"; routing answers "what should we use?"

**Invariant 6 — Memory does not become workflow state.** Persistent knowledge is retrieved into the workflow when needed, but the database stays outside the graph.

**Invariant 7 — Observation is dynamic.** The environment must be re-observed after meaningful actions or recovery events.

**Invariant 8 — Retrying is never assumed safe.** Retry behavior must account for idempotency, side effects, verification, and policy.

**Invariant 9 — Events are not the canonical task state.** Events describe or notify; graph state determines workflow truth.

**Invariant 10 — Every major decision should be explainable.** Operonix should eventually explain: why this plan, why this route, why this tool, why this safety decision, why this retry, why this replan, why this final outcome.

---

## 22. Updated Master Architecture

```
                              USER
                               │
                      Voice / Panel / API
                               │
                               ▼
                       OPERONIX RUNTIME
                               │
                ┌──────────────┴──────────────┐
                │                             │
                ▼                             ▼
            EventBus                     LangGraph
                │                             │
      system-wide events                OperonixState
                │                             │
                │                    ┌────────┼─────────┐
                │                    │        │         │
                │                    ▼        ▼         ▼
                │                 Context LangChain  Memory
                │                    │        │       /RAG
                │                    │        │         │
                │                    └────────┼─────────┘
                │                             │
                │                           PLAN
                │                             │
                │                          ROUTE
                │                             │
                │                    candidate discovery
                │                    candidate evaluation
                │                    policy constraints
                │                    ranking
                │                             │
                │                      MethodDecision
                │                             │
                │                           SAFETY
                │                             │
                │                    confirmation/policy
                │                             │
                │                         EXECUTOR
                │                             │
                │                    Capabilities/Plugins
                │                             │
                │                       Automation
                │                             │
                │                          OBSERVE
                │                             │
                │                          VERIFY
                │                             │
                │                 ┌───────────┴───────────┐
                │                 │                       │
                │              success                  failure
                │                 │                       │
                │              next step               RECOVER
                │                 │              ┌────────┼────────┐
                │                 │              │        │        │
                │                 │            retry    route    replan
                │                 │
                │                 ▼
                │              REFLECT
                │                 │
                │              FINALIZE
                │                 │
                └─────────────────┴──────────────► USER / UI
```

Supporting the entire system as cross-cutting concerns (not substitutes for the core graph): Safety, Context, Memory, Learning, Observability, Audit, Checkpointing, Runtime lifecycle, Configuration, Concurrency/resource management.

---

## 23. Scope Boundary for the Next Stage

The architecture now covers: **A** — State, **B** — Workflow, **C** — Module ownership, **D** — Interfaces, plus Persistence, Cancellation, Timeouts, Idempotency, Error semantics, Observability, Human intervention, Concurrency, Testing, Rollback, and Acceptance criteria.

The next task is **not another architecture redesign**. It's turning this architecture into an implementation strategy:

```
WHAT FIRST?
    ↓
WHAT CAN RUN IN PARALLEL?
    ↓
WHAT DEPENDS ON WHAT?
    ↓
WHAT MUST REMAIN STABLE?
    ↓
WHAT CAN BE MIGRATED SAFELY?
    ↓
WHEN DO WE RETIRE LEGACY COMPONENTS?
```

The implementation strategy must optimize for **minimum risk, maximum learning per migration step, and continuous operability of the existing Operonix system**.

---


# Part II — Priority & Implementation Strategy

## 24. Purpose of the Migration Strategy

The migration strategy exists to convert the architectural decisions in Parts A–D into a safe implementation program.

The strategy optimizes for:

```text
minimum disruption
+
maximum architectural learning per migration step
+
continuous operability of the existing system
+
clear rollback points
+
measurable reliability improvements
```

The guiding rule is:

> **Never migrate a subsystem merely because LangChain or LangGraph can technically replace it. Migrate it when doing so produces a measurable architectural advantage without sacrificing Operonix's existing reliability boundaries.**

---

## 25. Priority Model

Priority and implementation sequence are related but are **not the same thing**.

```text
Priority = architectural importance / risk / value

Sequence = dependency-aware order in which work is performed
```

Therefore a **P0** capability may be implemented in a later stage if it depends on earlier foundations.

### Priority classes

| Priority | Meaning | Typical examples |
|---|---|---|
| **P0 — Critical Foundation / Safety** | Must be established before dependent work is trusted | baseline tests, contracts, state, core graph path, safety preservation, verification |
| **P1 — Core Migration** | Establishes the new architectural spine | LangGraph runtime, LangChain model layer, intent/planning integration, first vertical slice |
| **P2 — Reliability / Expansion** | Makes the new architecture robust and broadly useful | recovery, idempotency, checkpointing, cancellation, routing modernization, tool adapters |
| **P3 — Adaptive / Advanced** | Adds experience-driven capability after reliable execution exists | RAG, memory integration, learning-driven routing |
| **P4 — Cleanup** | Removes proven duplication after replacement is stable | legacy orchestration retirement, repository cleanup |

### Priority rule

Safety, correctness, rollback capability, and observability take precedence over migration speed.

---

## 26. Migration Operating Model

Every meaningful migration stage follows the same loop:

```text
DESIGN
  ↓
IMPLEMENT ADAPTER / CHANGE
  ↓
UNIT + CONTRACT TEST
  ↓
INTEGRATE
  ↓
RUN REPRESENTATIVE WORKFLOW
  ↓
COMPARE WITH LEGACY
  ↓
OBSERVE
  ↓
STABILIZE
  ↓
PASS GATE
  ↓
NEXT STAGE
```

A stage is complete only when its exit criteria are satisfied.

### One-responsibility-at-a-time rule

A migration change should ideally answer one architectural question.

Examples:

```text
Add graph state

Add runtime ↔ graph boundary

Add LangChain model adapter

Move intent into graph

Connect planner

Connect existing executor

Add verification

Add recovery
```

Avoid large changes that simultaneously redefine state, routing, execution, and safety.

---

## 27. Migration Safety Model

The current Operonix system remains the behavioral baseline during migration.

```text
                 Operonix Runtime
                        │
                  feature control
                    /        \
                   ▼          ▼
             Legacy Path   Graph Path
```

The legacy path remains available until the graph path satisfies the corresponding acceptance criteria.

### Required migration controls

```text
known-good baseline
feature flags
contract tests
regression tests
observability
rollback point
```

### Legacy/new coexistence rule

Existing tasks that were started on the legacy workflow should be allowed to complete on that workflow during transitional releases unless an explicit migration/resume mechanism exists.

New tasks may be routed to the graph according to the active feature policy.

This avoids forcing an already-running legacy task through an incompatible state model.

---

## 28. Workstream Structure

The migration is organized into six major workstreams.

```text
W1 — Foundation
W2 — Graph Spine
W3 — AI Integration
W4 — Execution & Reliability
W5 — Routing / Tools / Knowledge
W6 — Legacy Retirement
```

Dependency view:

```text
                     W1 FOUNDATION
                          │
                ┌─────────┴─────────┐
                ▼                   ▼
           W2 GRAPH            W3 AI LAYER
                │                   │
                └─────────┬─────────┘
                          ▼
                 FIRST VERTICAL SLICE
                          │
                          ▼
                    W4 RELIABILITY
                          │
                 ┌────────┴────────┐
                 ▼                 ▼
          ROUTING / TOOLS      RAG / MEMORY
                 │                 │
                 └────────┬────────┘
                          ▼
                       LEARNING
                          │
                          ▼
                  W6 LEGACY RETIREMENT
```

### Parallelization principle

Work may proceed in parallel when it does not redefine a shared contract at the same time.

For example:

```text
Graph foundation
      ║
      ╠══ domain contracts
      ╠══ LangChain model adapter
      ╚══ baseline test expansion
```

But two efforts that simultaneously redefine `MethodDecision` should not proceed independently.

---

## 29. Dependency-Aware Implementation Roadmap

This is the **single authoritative migration sequence**. Earlier exploratory M1–M11/42-item sequences are superseded by this roadmap.

---

### Phase 0 — Baseline, Contracts & Safety

**Priority:** P0

**Goal:** establish the known-good foundation before introducing graph behavior.

#### Deliverables

```text
critical workflow inventory
regression baseline
known-good version / rollback point
feature flags
shared domain contracts
initial graph state schema
```

#### Scope

Representative baseline workflows should cover:

```text
simple file operation
application opening
shell operation
UI operation
web operation
multi-step workflow
failure + retry
failure + fallback/re-route
safety rejection
confirmation-required action
```

#### Entry condition

Current Operonix behavior can be exercised repeatably enough to establish a baseline.

#### Exit gate

We can answer:

> **What worked before the migration, and how will we detect a regression?**

#### Rollback

No architectural rollback is required yet; this phase creates the rollback baseline used by later phases.

---

### Phase 1 — Graph Foundation & Runtime Boundary

**Priority:** P0/P1

**Goal:** introduce LangGraph without changing the proven execution path.

#### Deliverables

```text
graph/state.py
graph/graph.py
graph/routing.py
graph/nodes/
Runtime ↔ Graph adapter
```

Initial topology:

```text
START
 ↓
INTAKE
 ↓
OBSERVE
 ↓
END
```

No AI and no executor rewrite are required for the first foundation pass.

#### Exit gate

A task can:

```text
enter runtime
→ become OperonixState
→ execute graph nodes
→ produce a terminal result
```

without requiring the graph to own EventBus, Executor, ToolRegistry, Context services, or persistent memory.

---

### Phase 2 — LangChain AI Bridge

**Priority:** P1

**Goal:** introduce LangChain beneath an Operonix-owned model interface.

#### Architecture

```text
Operonix ModelService
        ↓
     LangChain
        ↓
Ollama / Groq / Gemini / OpenRouter
```

The existing provider-independence is preserved.

#### First migration target

Structured intent interpretation.

```text
User request
    ↓
LangGraph
    ↓
analyze_intent
    ↓
LangChain
    ↓
IntentResult
    ↓
deterministic Operonix resolution
```

#### Exit gate

No subsystem outside the AI boundary depends directly on LangChain-specific model objects, and representative intent tests remain behaviorally acceptable versus the legacy implementation.

---

### Phase 3 — Planning Integration

**Priority:** P1

**Goal:** place planning inside graph control while preserving the existing planner's deterministic/AI split.

```text
CREATE_PLAN
     │
     ├── simple → deterministic plan
     │
     └── complex → LangChain-backed plan
```

The graph owns:

```text
current step
completed steps
workflow position
```

The planner owns:

```text
what the steps are
```

#### Exit gate

`Plan` and `PlanStep` are valid domain objects that can be consumed by the existing routing/safety/execution path.

---

### Phase 4 — First Vertical Slice

**Priority:** P0 milestone

**Goal:** prove the new architecture end-to-end without replacing the operational foundation.

#### Canonical workflow

```text
"Open Firefox and search for autonomous agents"
```

This workflow is already the documented representative Operonix flow.

#### Initial graph path

```text
START
 ↓
INTAKE
 ↓
OBSERVE
 ↓
ANALYZE_INTENT
 ↓
RETRIEVE_KNOWLEDGE (may be a no-op initially)
 ↓
CREATE_PLAN
 ↓
ROUTE (existing router behind adapter)
 ↓
SAFETY_CHECK
 ↓
EXECUTE_STEP (existing Executor)
 ↓
OBSERVE
 ↓
VERIFY
 ↓
REFLECT
 ↓
FINALIZE
```

Failure routes to recovery.

#### Important constraint

Do **not** require the new candidate-ranking router or new LangChain tool adapter to finish this milestone. The goal is to prove graph orchestration against trusted existing systems.

#### Exit gate

The workflow completes correctly through:

```text
LangGraph
→ existing IntentParser
→ existing Planner
→ existing routing
→ existing Safety
→ existing Executor
→ existing Automation
→ verification
→ existing Reflector
```

with observable results and a working rollback path.

---

### Phase 5 — Verification & Recovery

**Priority:** P0

**Goal:** make the graph genuinely stateful and reliable rather than simply replacing event sequencing.

#### 5.1 Verification

```text
EXECUTE
 ↓
OBSERVE
 ↓
VERIFY
```

The system must distinguish:

```text
executor reported success
```

from:

```text
intended postcondition verified
```

#### 5.2 Recovery

```text
VERIFY failure
      ↓
   RECOVER
      ├── retry
      ├── observe
      ├── route
      └── replan
```

#### 5.3 Error semantics

Integrate the shared error taxonomy and recovery mapping defined in Part D.

#### Exit gate

At minimum the graph correctly demonstrates:

```text
transient failure
context mismatch
routing mismatch
tool unavailable
verification failure
planning failure
```

and returns to the appropriate stage rather than applying one universal fallback path.

---

### Phase 6 — Idempotency, Side Effects & Safe Re-execution

**Priority:** P0

**Goal:** make retries and resume semantics safe.

#### Required semantics

```text
idempotency
side-effect level
reversibility
preconditions
postconditions
retry policy
```

#### Required behavior

A failed or interrupted side-effecting operation must not be assumed to have had no effect.

```text
failure
  ↓
observe
  ↓
check postcondition
  ↓
already happened?
  ├── yes → verify / continue
  └── no  → retry / recover
```

#### Special outcome

Introduce a semantic **UNCERTAIN_OUTCOME** classification for cases where the system cannot determine whether a side effect occurred.

This must not be treated as an ordinary failure.

#### Exit gate

Representative non-idempotent/side-effecting actions do not blindly duplicate themselves during retry or resume tests.

---

### Phase 7 — Checkpointing, Pause/Resume & Human Intervention

**Priority:** P1

**Goal:** allow workflows to survive interruption and wait for people without reintroducing manual event-driven task bookkeeping.

#### Checkpointing

Persist enough information to resume:

```text
task identity
workflow state
current node
current plan step
completed steps
routing decision
safety/confirmation state
execution status
recovery data
relevant context
state version
timestamp
```

#### Confirmation flow

```text
SAFETY_CHECK
 ↓
CONFIRMATION_REQUIRED
 ↓
checkpoint
 ↓
PAUSE
 ↓
human response
 ↓
RESUME
```

#### Human intervention

Initial implementation:

```text
CONFIRM
DENY
```

Future-capable contract:

```text
CLARIFY
CHOOSE
PROVIDE_INFORMATION
TAKE_OVER
ABORT
```

#### Exit gate

A workflow can pause, survive process interruption, resume, re-observe the environment, and safely continue without losing task state or duplicating side effects.

---

### Phase 8 — Cancellation, Timeout & Resource Control

**Priority:** P1

**Goal:** make long-running and concurrent workflows controllable.

#### Implement

```text
workflow cancellation
operation timeout
step timeout
task timeout
system/watchdog timeout
safe abort semantics
resource ownership
```

#### Resource rule

```text
logical workflow concurrency
        ≠
physical desktop concurrency
```

Graph instances may coexist, but physical resources such as keyboard, mouse, active window, and focus may require serialization.

#### Exit gate

Tests cover:

```text
user cancellation
step timeout
task timeout
safe abort
resource contention
```

---

### Phase 9 — Observability & Execution Trace

**Priority:** P1

**Goal:** make the new architecture inspectable enough to trust and debug.

The trace should expose:

```text
request
intent
context
retrieved knowledge
plan
routing candidates
routing decision
safety decision
execution attempts
observations
verification
recovery
reflection
final outcome
```

EventBus publishes observability events; it does not become the task state machine again.

#### Exit gate

For a failed task, a developer can reconstruct:

```text
what happened
why it happened
what was selected
why alternatives were rejected
what failed
why recovery occurred
what final result was produced
```

---

### Phase 10 — Candidate-Based Routing Engine

**Priority:** P1/P2

**Goal:** replace the old fixed routing hierarchy with an extensible decision system.

#### Architecture

```text
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

Candidates may include:

```text
plugins
APIs
shell
UI
browser automation
vision
future computer-use mechanisms
remote/local capabilities
```

#### Inputs to ranking

```text
capability fit
context fit
availability
reliability
historical success
risk
permissions
latency
reversibility
runtime/deployment policy
```

#### Important invariant

`PLUGIN → API → SHELL → UI` is **not** the architectural priority order.

Those are execution categories. A deployment may configure preferences, but the routing architecture evaluates candidates for the specific step.

#### Exit gate

Routing regression tests show that the new engine selects viable methods correctly across availability, permission, context, reliability, and failure scenarios, without hidden fixed-priority behavior.

---

### Phase 11 — Tool Adapter Architecture

**Priority:** P2

**Goal:** expose Operonix capabilities to LangChain without bypassing Operonix controls.

```text
LangChain Tool
      ↓
Operonix Tool Adapter
      ↓
BaseTool
      ↓
Safety / Executor
      ↓
Capability / Plugin
```

The existing `BaseTool`, `ToolRegistry`, capabilities, and plugins remain the implementation foundation.

#### Exit gate

Migrated tools execute through the existing safety and executor boundaries and remain independently testable without LangChain.

---

### Phase 12 — Plugin Integration

**Priority:** P2

**Goal:** make plugins first-class routing/tool candidates.

```text
Plugin Manifest
      ↓
Capability Descriptor
      ↓
Routing Candidate
      ↓
Safety
      ↓
Executor
      ↓
Sandboxed Plugin
```

#### Exit gate

A valid installed plugin can register its capabilities, become discoverable, pass policy/safety, execute, and remain sandboxed.

---

### Phase 13 — RAG, Memory & Knowledge Provenance

**Priority:** P2/P3

**Goal:** add relevant persistent knowledge only after the basic workflow is reliable.

```text
RETRIEVE_KNOWLEDGE
      ↓
Memory / RAG Services
      ↓
KnowledgeContext
      ↓
Planner / Agent
```

Persistent stores remain outside graph state.

The graph receives relevant information and, eventually, provenance describing which retrieved items contributed to reasoning.

#### Exit gate

Knowledge retrieval improves relevant workflows without making simple workflows unnecessarily expensive or loading entire databases into graph state.

---

### Phase 14 — Learning-Driven Routing & Adaptation

**Priority:** P3

**Goal:** let reliable execution history improve future routing and planning decisions.

```text
Execution
 ↓
Verification
 ↓
Reflection
 ↓
Learning
 ↓
Historical signals
 ↓
Candidate ranking
```

The first learning target should be **performance feedback**, not unrestricted self-modification.

Learning may influence ranking and planning hints only through explicit policy-controlled interfaces.

#### Exit gate

Historical performance can improve candidate ranking without bypassing safety, policy, permissions, or deterministic execution controls.

---

### Phase 15 — Legacy Retirement

**Priority:** P4 / last

**Goal:** remove duplicated orchestration only after the graph is demonstrably stable.

Potential retirement order:

```text
goal_stack.py
 ↓
duplicated routing logic
 ↓
DecisionEngine
 ↓
duplicated ToolSelector logic
 ↓
redundant CapabilityMapper routing
 ↓
active_tasks as workflow authority
 ↓
old AI event-chain orchestration
 ↓
legacy LLM implementation
 ↓
obsolete orchestrator task lifecycle
```

The actual retirement order is dependency-driven; this is the expected direction, not a license to delete early.

#### Exit gate

For each retired component:

```text
replacement operational
replacement tested
behavior verified
no required caller remains
observability sufficient
rollback understood
```

---

## 30. Parallel Work Plan

Some workstreams can progress concurrently after their prerequisites are stable.

### Early parallelism

```text
FOUNDATION
   ├── domain contracts
   ├── graph skeleton
   ├── LangChain model adapter
   └── baseline test expansion
```

### After first vertical slice

```text
RELIABILITY
   ├── verification
   ├── recovery
   ├── idempotency
   ├── checkpointing
   └── observability
```

These may overlap where contracts are already frozen.

### Later parallelism

```text
ROUTING ENGINE ──────────┐
                         ├──► broader graph migration
TOOL ADAPTERS ───────────┤
                         │
PLUGIN INTEGRATION ──────┘

RAG / MEMORY ───────────────► Learning
```

Avoid parallel changes that simultaneously redefine the same domain contract.

---

## 31. Migration Gates

### Gate A — Foundation

```text
baseline tests
contracts
OperonixState
feature control
```

### Gate B — Graph Integration

```text
runtime ↔ graph
LangChain model boundary
intent
planning
```

### Gate C — Vertical Slice

```text
real task completes end-to-end
existing safety/executor remain intact
trace available
```

### Gate D — Reliability

```text
verification
recovery
idempotency
checkpointing
cancellation/timeouts
```

### Gate E — Modernization

```text
candidate routing
tool adapters
plugins
RAG/memory
```

### Gate F — Retirement

```text
legacy duplication no longer required
regression passes
rollback understood
```

No destructive architectural cleanup should cross Gate F conditions prematurely.

---

## 32. Risk Ranking

| Area | Risk | Strategy |
|---|---|---|
| Domain contracts | Low–Medium | introduce incrementally, test boundaries |
| Graph skeleton | Low | no behavior change initially |
| LangChain model adapter | Low–Medium | preserve provider abstraction |
| Intent migration | Medium | retain deterministic resolution/fallback |
| Planner migration | Medium | preserve deterministic planner path |
| Existing Executor integration | Medium | thin adapter, no executor rewrite |
| Verification/recovery | Medium–High | build against real failure scenarios |
| Checkpoint/resume | High | combine with idempotency + observation |
| Routing rewrite | High | keep legacy router behind adapter/flag |
| Tool adapters | Medium | preserve BaseTool/executor/safety path |
| Plugin integration | Medium–High | preserve sandbox/manifest controls |
| RAG/memory | Medium | add after workflow stability |
| Learning-driven routing | High | feed only through controlled signals |
| Safety architecture rewrite | Very High | avoid; preserve deterministic boundary |
| Automation engine rewrite | Very High | avoid; preserve existing implementation |
| Executor rewrite | Very High | avoid; preserve existing implementation |
| Early legacy deletion | Very High | prohibit until retirement gate |

---

## 33. What Must Never Be Migrated Early

Do not begin the migration by rewriting:

```text
Executor
Safety system
WindowDetector
ScreenReader
SelectorEngine
VisionModel
FocusManager
Capabilities
Plugin sandbox
```

These are Operonix's operational foundation.

LangChain/LangGraph should orchestrate and connect them, not replace them simply because replacement is technically possible.

---

## 34. Progress Measurement

Migration progress should be measured by architectural outcomes, not by the number of files moved.

Useful metrics include:

```text
percentage of task lifecycle controlled by LangGraph
percentage of AI interactions routed through LangChain
percentage of tools exposed through the standard adapter
percentage of execution steps with verification
percentage of failures with classified recovery
percentage of workflows supported by checkpoint/resume
percentage of tasks still dependent on legacy orchestration
```

The final target is not "100% LangChain".

The target is:

```text
maximum useful LangChain/LangGraph adoption
while preserving the strongest Operonix deterministic boundaries
```

---

## 35. Testing Strategy for the Migration

The migration is complete only when the new architecture demonstrates correctness, safety, reliability, and compatibility.

### 35.1 Unit tests

Test domain services independently:

```text
IntentParser
Planner
RoutingEngine
Safety
Executor
Verification
Reflector
Tool adapters
```

### 35.2 Contract tests

Verify every important boundary:

```text
Graph → Planner
Graph → Routing
Graph → Safety
Graph → Executor
LangChain → Tool Adapter
Tool Adapter → BaseTool
Plugin → Capability Registry
```

### 35.3 Graph-node tests

```text
given state
    ↓
node
    ↓
expected state delta
```

### 35.4 Routing tests

Cover:

```text
candidate unavailable
permission denied
context changed
plugin unhealthy
API unavailable
historical ranking changed
policy preference changed
multiple viable candidates
no viable candidate
```

### 35.5 Safety tests

Cover:

```text
allowed
blocked
confirmation required
malformed tool call
unsafe parameters
permission failure
policy violation
```

### 35.6 Recovery tests

Cover:

```text
retry
re-observe
re-route
re-plan
uncertain outcome
timeout
cancellation
partial completion
```

### 35.7 Regression tests

For representative workflows:

```text
same request
   ├── legacy
   └── graph
        ↓
      compare
```

Compare externally meaningful behavior, including:

```text
success/failure
safety outcome
side effects
final result
verification outcome
```

Exact internal sequences do not have to match.

### 35.8 Shadow/comparison mode

Where safe, the graph should be allowed to produce decisions that can be compared against the legacy path without automatically replacing legacy execution.

---

## 36. Observability Requirements During Migration

A migration stage should not become harder to debug than the architecture it replaces.

Every migrated workflow should expose at least:

```text
workflow ID / task ID
current node
plan step
routing decision
safety decision
execution result
verification result
recovery decision
final outcome
```

The EventBus can publish these as observable events.

The graph state remains the canonical workflow state.

---

## 37. State, Checkpoint & Legacy Compatibility Rules

### 37.1 Three distinct persistence concepts

```text
Graph State
    = current workflow state

Checkpoint
    = resumable persisted workflow state

Memory
    = long-lived knowledge / experience
```

### 37.2 Legacy state compatibility

During transition:

```text
legacy task already running
        ↓
finish on legacy path
```

while:

```text
new task
   ↓
new graph path
```

unless explicit migration/resume conversion exists.

### 37.3 State versioning

Checkpoints must carry:

```text
state_schema_version
workflow_version
```

so incompatible states are detected before resume.

---

## 38. Human Intervention & Runtime Control Requirements

Human intervention and runtime control are not edge features; they are part of safe autonomy.

The workflow must eventually support:

```text
pause
resume
confirm
deny
clarify
provide information
take over
abort
cancel
```

The first implementation may support only the smallest required subset, beginning with confirmation.

---

## 39. Out of Scope for the Initial Migration

The initial migration will **not** attempt to:

```text
rewrite the automation engine
rewrite the Executor
rewrite the deterministic safety model
rewrite the plugin sandbox
redesign voice/STT/TTS
redesign the dashboard
implement unrestricted autonomous self-modification
migrate every capability simultaneously
introduce distributed cloud execution as part of the first slice
replace every EventBus event with graph state
```

These may be future projects, but they should not become hidden dependencies of the first LangGraph migration.

---

## 40. Final Implementation Roadmap

The roadmap is intentionally compact here; the detailed phase definitions above are authoritative.

```text
PHASE 0
Baseline + Contracts + Safety
        │
        ▼
PHASE 1
Graph Foundation + Runtime Boundary
        │
        ▼
PHASE 2
LangChain AI Bridge
        │
        ▼
PHASE 3
Planning Integration
        │
        ▼
PHASE 4
First Vertical Slice
        │
        ▼
PHASE 5
Verification + Recovery
        │
        ▼
PHASE 6
Idempotency + Side-Effect Safety
        │
        ▼
PHASE 7
Checkpointing + Human Intervention
        │
        ▼
PHASE 8
Cancellation + Timeout + Resource Control
        │
        ▼
PHASE 9
Observability / Execution Trace
        │
        ▼
PHASE 10
Candidate-Based Routing
        │
        ▼
PHASE 11
Tool Adapters
        │
        ▼
PHASE 12
Plugin Integration
        │
        ▼
PHASE 13
RAG + Memory + Provenance
        │
        ▼
PHASE 14
Learning-Driven Adaptation
        │
        ▼
PHASE 15
Legacy Retirement
        │
        ▼
OPERONIX vNext
```

---

## 41. Definition of Successful Migration

The migration is successful when:

```text
Operonix
    ↓
LangGraph owns task workflow
    ↓
LangChain owns AI primitives
    ↓
Routing owns execution-method selection
    ↓
Safety still controls authorization
    ↓
Executor still performs reliable execution
    ↓
Capabilities / Plugins still own real operations
    ↓
Context remains dynamically observable
    ↓
Verification confirms meaningful outcomes
    ↓
Recovery is state-aware
    ↓
Workflows can pause/resume safely
    ↓
Execution is observable
    ↓
Memory/Learning can improve future behavior
    ↓
legacy orchestration is no longer required
```

The goal is **not** to maximize the amount of LangChain/LangGraph in the repository.

The goal is to produce a more explicit, stateful, reliable, observable, extensible, and maintainable Operonix architecture while preserving the deterministic systems that make real desktop automation safe.

---

## 42. Final Architectural Invariants

These invariants apply throughout implementation and future growth.

1. **One task → one canonical workflow state.**
2. **State stores workflow data, not service objects.**
3. **Services return domain results; graph nodes update graph state.**
4. **EventBus describes/integrates system behavior but does not reconstruct task state.**
5. **Planner decides what; Router decides how, per step.**
6. **Routing is candidate-based, not a universal hard-coded priority order.**
7. **Safety constrains candidate selection and authorizes final execution.**
8. **The LLM never receives unrestricted execution authority.**
9. **Executor performs actions; LangGraph determines workflow strategy.**
10. **Observation is dynamic; the environment is never assumed static after meaningful actions.**
11. **A failed execution does not prove that no side effect occurred.**
12. **Retry requires idempotency/side-effect awareness and, where necessary, postcondition verification.**
13. **Checkpointed workflows must be resumable without blindly repeating uncertain actions.**
14. **Human intervention is a workflow pause/resume mechanism, not a second orchestration system.**
15. **Persistent memory remains outside graph state.**
16. **Legacy retirement is evidence-driven and reversible until proven stable.**
17. **Major decisions must be observable and explainable.**
18. **The architecture must remain extensible as new execution mechanisms are added.**

---

## 43. Final Planning Status

```text
Architecture
    ✅ Current architecture understood
    ✅ Target architecture defined
    ✅ Part A — State defined
    ✅ Part B — Graph topology defined
    ✅ Part C — Module ownership defined
    ✅ Part D — Interfaces/contracts defined

Reliability
    ✅ Checkpointing
    ✅ Cancellation/timeouts
    ✅ Idempotency / side effects
    ✅ Error taxonomy
    ✅ Verification / recovery
    ✅ Observability
    ✅ Human intervention
    ✅ Concurrency / resource ownership

Migration
    ✅ Priority model
    ✅ Workstreams
    ✅ Dependency-aware sequence
    ✅ Parallelization rules
    ✅ Migration gates
    ✅ Risk ranking
    ✅ Testing strategy
    ✅ Rollback / feature control
    ✅ Acceptance criteria
    ✅ Out-of-scope boundaries

Result
    ✅ Master migration plan ready for implementation
```

> **Implementation begins with Phase 0. The architecture should not be redesigned again unless actual implementation evidence reveals a contradiction with these invariants or the real Operonix behavior.**
