# AURUM — Codebase Overview (Phase 1) — Refreshed Engineering Guide

## Summary
AURUM is a Python CLI agent that converts a user **goal** into an LLM-generated sequence of **tool calls**, executes those steps in order, evaluates each step’s outcome using heuristics, and only declares the goal complete after a **rule-based verification** gate. It persists run history to a local SQLite database (`aurum_state.db`) and uses a three-layer memory abstraction (short-term in-memory, long-term facts, episodic histories) to inform planning. The implementation is intentionally small and “tool-first”, but several decision points are currently driven by **unstructured string outputs**, making the system behavior sensitive to phrasing.

## Architecture
**Primary pattern:** an **agent orchestration loop** (single-threaded orchestration) that coordinates:
- **Planner** (`core/planner.py`) to create/replan a JSON plan of tool calls
- **Executor** (`core/executor.py`) to invoke tool functions by name
- **Evaluator** (`core/evaluator.py`) to label each executed step as passed/failed using keyword heuristics
- **Verifier** (`core/verifier.py`) to validate completion evidence when the planner emits a `done` tool step

**Technology stack**
- Language/runtime: Python 3
- LLM planning: Groq SDK (`core/planner.py`)
- CLI UX: `rich`
- Web search: `ddgs` (DuckDuckGo text aggregation)
- Persistence: SQLite (`state/persistence.py`)
- Tools: implemented in `tools/tool_registry.py`
- Memory: SQLite-backed memory plus an in-memory short-term dict (`memory/memory_manager.py`)

**Where execution starts**
- `main.py` loads `.env`, validates the Groq key, prints a banner, then loops:
  - reads user input via `interface/goal_input.py`
  - runs the goal via `Orchestrator.run(goal)` (`core/orchestrator.py`)

## Directory Structure
```
project-root/
├── main.py
├── play.py                         # unrelated example script
├── README.md
├── requirements.txt
├── aurum_state.db                 # SQLite persistence
├── core/
│   ├── orchestrator.py             # central agent loop + placeholder injection
│   ├── planner.py                  # LLM prompts, plan JSON parsing, replan
│   ├── executor.py                 # executes a single plan step
│   ├── evaluator.py                # heuristic step evaluation + loop detection
│   └── verifier.py                 # rule-based goal verification after done
├── tools/
│   └── tool_registry.py           # tool implementations + registry
├── memory/
│   └── memory_manager.py         # short/long/episodic memory layers
├── state/
│   └── persistence.py            # SQLite schema + CRUD for goals/steps
└── interface/
    └── goal_input.py             # CLI input and history display
```

## Key Abstractions

### Orchestrator
- **File**: `core/orchestrator.py`
- **Responsibility**: Orchestrates the full loop:
  1. persists goal (`save_goal`)
  2. builds a plan (`create_plan`)
  3. iterates steps (placeholder substitution → loop detection → tool execution → evaluation)
  4. persists each executed step (`save_step`)
  5. triggers replanning when a step fails or when verification fails after `done`
  6. generates a final “completion summary” via LLM and records episodic memory
- **Important internal mechanics**
  - Placeholder injection is performed *before* executing each step.
  - Loop detection is checked before running a tool.
  - `done` does **not** immediately mean success: the orchestrator gates completion on `verify_goal(...)`.
- **Used by**: `main.py`

### Planner
- **File**: `core/planner.py`
- **Responsibility**: Produces the plan JSON expected by the orchestrator.
- **Key interfaces**
  - `create_plan(goal, context="", memory=None) -> dict`
  - `replan(goal, completed_steps, failed_step, failure_reason, memory=None) -> dict`
- **Key behavior**
  - The planner prompt strongly instructs that plans must end with a final `done` step.
  - There is special “fallback plan” logic for GUI/game-like or some code-generation goals:
    - either template-based code (`TODO_TEMPLATE`, `TETRIS_TEMPLATE`)
    - or LLM-generated raw Python code that is wrapped into `file_write` + `done`.

### Executor
- **File**: `core/executor.py`
- **Responsibility**: Executes exactly one plan step.
- **Key interface**
  - `run_step(step: dict) -> dict`
- **Key behavior**
  - Special-cases `tool == "done"` (returns `status: "done"`).
  - For non-terminal tools, it calls `execute_tool(tool, tool_input)` from `tools/tool_registry.py`.
  - It sets `status` using brittle string matching over the tool’s raw textual output.

### Evaluator
- **File**: `core/evaluator.py`
- **Responsibility**: Converts execution results into an `evaluation.passed` boolean plus a reason.
- **Key behavior**
  - If `status == "failed"` → step fails.
  - If result text contains “success keywords” or “failure phrases/prefixes” → pass/fail.
  - If result is empty/very short → fail.
  - `done` is treated as passed at the evaluator layer (the orchestrator still requires verifier success later).

### Verifier (goal-level)
- **File**: `core/verifier.py`
- **Responsibility**: Decides whether the overall goal evidence is sufficient to accept completion.
- **Key interface**
  - `verify_goal(goal, completed_steps, attempted_steps=None) -> dict`
- **Key behavior**
  - Infers what the goal required by pattern matching on the goal text:
    - whether it’s a research/search goal
    - whether it asks for a summary
    - whether it mentions reading/writing filenames
    - whether it’s a code generation / code-writing request
    - whether it’s a calculation request
  - Checks for evidence by:
    - filename extraction from goal text (regex)
    - file existence and non-emptiness
    - Python syntax validation for `.py` files (AST parse)
    - web_search presence for research goals
    - summary plausibility checks (length + “snippet marker” heuristics)

### Tools / Tool Registry
- **File**: `tools/tool_registry.py`
- **Responsibility**: Implements tool functions and exposes them to the planner.
- **Tools**
  - `web_search(query, max_results)` → DDGS snippet aggregation (returns **text snippets**)
  - `summarize_text(text, goal, max_items)` → produces Markdown “Summary” with sources
  - `file_read(path)` → raw file contents
  - `file_write(path, content)` → writes text; for `.py` validates AST parse before writing
  - `file_list(directory)` → directory listing
  - `run_python(code)` → runs code in a temporary file with timeout and blocked patterns (not a real sandbox)
- **Key constraint (current reality)**: all tools return **raw strings** only (no structured success/fail payload). Downstream logic infers success from those strings.

### Memory System
- **File**: `memory/memory_manager.py`
- **Responsibility**: Provides three memory layers backed by SQLite (long-term + episodic) and an in-memory dict (short-term).
- **Key interfaces**
  - Short-term: `set/get/snapshot`
  - Long-term: `set/get/all` stores JSON values
  - Episodic: `record(goal_id, goal_text, outcome, strategy_notes)` and `recall_recent(limit)`
- **Planner coupling risk**
  - `core/planner.py` attempts to render episodic episodes into context using expected keys; any mismatch between stored structure and expected access patterns will degrade planning quality.

### Persistence
- **File**: `state/persistence.py`
- **Responsibility**: Maintains SQLite schema and CRUD for:
  - `goals` (id, goal_text, status, timestamps)
  - `steps` (goal_id, step_index, tool, tool_input, result, status)
  - `memory` table (used by memory layers)
- **Lifecycle behavior**
  - On startup, orchestrator calls `reset_orphaned_goals()` which sets any goal in `running` status to `failed`.

## Data Flow
1. User enters goal in CLI:
   - `interface/goal_input.py::prompt_goal()`
2. Start agent loop:
   - `main.py` creates an `Orchestrator` and calls `Orchestrator.run(goal)`
3. Persist and initialize:
   - `Orchestrator` calls `save_goal(..., status="running")`
   - sets `MemoryManager.short` fields (`goal`, `goal_id`)
4. Plan generation:
   - `core/planner.create_plan()` calls Groq and parses returned JSON into `{plan_summary, steps}`
5. Per-step loop (bounded by `MAX_STEPS`):
   - placeholder resolution in `core/orchestrator.py`:
     - `_previous_context(step_results)` is concatenated prior step outputs
     - special handling for `.py` content substitutions for `file_write`
   - loop detection via `core/evaluator.check_loop_detection(...)`
   - tool execution via `core/executor.run_step(...)` → `tools/tool_registry.execute_tool(...)`
   - evaluation via `core/evaluator.evaluate_step(...)` → `evaluation.passed/reason`
   - persistence via `state.persistence.save_step(...)`
6. Terminal completion path:
   - when executor returns `status == "done"`, orchestrator calls:
     - `core.verifier.verify_goal(goal, completed, attempted)`
   - if verifier passes: mark goal completed and generate a final summary
   - if verifier fails: orchestrator replans and continues (bounded by `MAX_RETRIES`)

## Non-Obvious Behaviors & Design Decisions (what surprises new engineers)

1. **String-based “success” is a first-class design dependency (currently).**
   - `core/executor` and `core/evaluator` infer status/passed from tool output text using keyword/prefix matching.
   - Any change in wording from tools can flip statuses even if the tool genuinely did the right work.

2. **The planner’s JSON contract is enforced only structurally; semantic safety is not.**
   - The planner prompt says “return ONLY valid JSON” and includes a final `done` step.
   - But the system still relies on verifier heuristics afterward rather than hard semantic guarantees.

3. **Completion is gated (good), but other steps can still drift into false positives.**
   - Verifier prevents fake “done” acceptance.
   - However, earlier step pass/fail can still be misclassified due to heuristic evaluation.

4. **Placeholder substitution can lead to context explosion.**
   - `_previous_context()` concatenates **all** previous step results for `{...}` placeholder replacement.
   - This can flood later tool inputs with irrelevant earlier output, and can accidentally steer the tool behavior.

5. **Replanning logic exists but is not deterministic enough for “strict rules” work.**
   - The orchestrator replans on evaluator failure and on verifier failure, with retry limits.
   - It does not enforce a constrained multi-plan scoring strategy or structured replanning triggers.

6. **Tool safety is only partially handled.**
   - `run_python` blocks some patterns and sets a timeout, but it is not a sandbox.
   - It is safer than unrestricted execution, but not “secure execution”.

## Module Reference (quick map)

| File | Purpose |
|---|---|
| `main.py` | CLI loop, env validation, starts orchestrator |
| `core/orchestrator.py` | Main agent loop; placeholder injection; retries and verification gating |
| `core/planner.py` | LLM prompting; plan JSON; fallback code plan generation |
| `core/executor.py` | Executes tool steps and sets `status` heuristically |
| `core/evaluator.py` | Heuristic evaluation of step output into passed/failed |
| `core/verifier.py` | Rule-based goal verification from evidence |
| `tools/tool_registry.py` | Tool implementations (web_search, summarize_text, file ops, run_python) |
| `memory/memory_manager.py` | Short-term, long-term, episodic memory layers |
| `state/persistence.py` | SQLite schema + persistence for goals/steps/memory |

## Suggested Reading Order
1. `main.py`
2. `core/orchestrator.py`
3. `core/planner.py`
4. `tools/tool_registry.py`
5. `core/executor.py` → `core/evaluator.py`
6. `core/verifier.py`
7. `state/persistence.py` and `memory/memory_manager.py`
