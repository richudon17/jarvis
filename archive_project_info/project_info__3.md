# AURUM — Codebase Overview (Phase 1) — Updated Master Notes

## Summary
AURUM is a Python CLI autonomous agent that turns a user **goal** into an LLM-generated sequence of **tool calls**, executes them in order, **evaluates** each step using heuristics, and only marks the goal complete after **rule-based verification** of evidence. It persists goals and step records to a local SQLite DB (`aurum_state.db`) so you can inspect history across runs. The core execution loop is in `core/orchestrator.py`, while tools and tool outputs drive most of the system’s decision-making.

## Architecture
**Primary architectural pattern:** an **agent orchestration loop** (central control flow) composed of 4 internal modules:
1. **Planner** (`core/planner.py`) — generates a JSON plan for tools, including a final `done` step.
2. **Executor** (`core/executor.py`) — runs the named tool with tool_input parameters.
3. **Evaluator** (`core/evaluator.py`) — determines `passed/failed` based on unstructured result text.
4. **Verifier** (`core/verifier.py`) — performs goal-level checks after the `done` step.

**Tech stack / runtime:**
- Python, CLI UI via `rich`
- LLM planning via Groq SDK (`core/planner.py`), expecting JSON output
- Web search via `ddgs` (DuckDuckGo aggregator) and local file ops / python subprocess via `tools/tool_registry.py`
- Persistence via SQLite (`state/persistence.py`, DB: `aurum_state.db`)

**How execution starts:**
- `main.py` loads `.env`, validates `GROQ_API_KEY`, then loops:
  - `interface/goal_input.prompt_goal()` → `Orchestrator.run(goal)`
- The orchestrator:
  - initializes DB + memory
  - requests a plan
  - iterates steps until completion/failure/limits
  - verifies goal evidence after `done`

## Directory Structure
```
project-root/
├── main.py
├── core/
│   ├── orchestrator.py
│   ├── planner.py
│   ├── executor.py
│   ├── evaluator.py
│   └── verifier.py
├── tools/
│   └── tool_registry.py
├── memory/
│   └── memory_manager.py
├── state/
│   └── persistence.py
├── interface/
│   └── goal_input.py
├── aurum_state.db
└── (misc) README.md, requirements.txt
```

## Key Abstractions

### Orchestrator
- **File**: `core/orchestrator.py`
- **Responsibility**: End-to-end orchestration loop with:
  - placeholder substitution across tool inputs
  - execution of each tool step
  - evaluation + persistence
  - replanning on failure and goal verification after `done`
- **Interface**:
  - `Orchestrator.run(goal: str, goal_id: str | None = None) -> str`
- **Lifecycle & state**:
  - Constructed per CLI run
  - Calls `reset_orphaned_goals()` + `init_db()` on startup
  - Holds in-memory short-term memory (`self.memory`)
- **Used by**: `main.py`

### Planner (create_plan / replan)
- **File**: `core/planner.py`
- **Responsibility**: Generate JSON plans constrained to tool calls and include a terminal `done` step.
- **Interface**:
  - `create_plan(goal, context="", memory=None) -> dict`
  - `replan(goal, completed_steps, failed_step, failure_reason, memory=None) -> dict`
- **Non-obvious behavior**:
  - “GUI/game-like goals” and some code generation requests trigger **template/raw-code fallback plans** instead of normal LLM plan generation.
  - There are vestigial or inconsistent branches related to memory injection for episodic recall.

### Executor
- **File**: `core/executor.py`
- **Responsibility**: Executes one tool call; special-cases `"done"`.
- **Interface**:
  - `run_step(step: dict) -> dict` (adds `result` and `status`)
- **Critical behavior**:
  - Tool `status` is derived from string matching over the tool’s returned text.
  - This `status` is later used by `core/evaluator.evaluate_step`.

### Evaluator
- **File**: `core/evaluator.py`
- **Responsibility**: Adds `evaluation: {passed, reason}` to each step.
- **Heuristics**:
  - If `status == "failed"` → immediate fail
  - If output contains success keywords → pass
  - If output contains known failure phrases/prefixes → fail
  - If output is empty or too short → fail
  - If step is `done` tool → passed at evaluator layer (still requires verifier later)

### Verifier (goal-level)
- **File**: `core/verifier.py`
- **Responsibility**: Confirm evidence satisfies the goal before completion is accepted.
- **Checks are rule-based** and depend on inferring:
  - expected filenames from the goal text (regex)
  - whether the goal asks to save/write/summarize, or only research/read/calculate
- **Most important invariant**:
  - completion is only returned when `verify_goal(...)[passed]` is true after a `done` step.

### Tool Registry
- **File**: `tools/tool_registry.py`
- **Responsibility**: Implements the actual tool functions the planner can request.
- **Tools**:
  - `web_search(query, max_results)`
  - `summarize_text(text, goal, max_items)`
  - `file_read(path)`
  - `file_write(path, content)` (Python syntax validation for `.py`)
  - `file_list(directory)`
  - `run_python(code)` (subprocess with timeout + blocking heuristics)
- **Non-obvious coupling**:
  - Tools return **raw strings**, and the rest of the system infers `status` and evaluation purely by matching phrases in those strings.

### Memory system
- **File**: `memory/memory_manager.py`
- **Responsibility**: Three-layer memory:
  - Short-term: in-memory key/value for current session
  - Long-term: SQLite facts (key/value)
  - Episodic: SQLite history of goal/outcome/strategy notes
- **Coupling with planner**:
  - `core/planner.create_plan` tries to recall episodic episodes and render them into planner context, with potential schema/key mismatches.

## Data Flow
1. **Goal input**: `interface/goal_input.prompt_goal()` returns a string.
2. **Agent kickoff**: `main.py` calls `Orchestrator.run(goal)`.
3. **Persistence + memory**:
   - `state/persistence.save_goal(..., status="running")`
   - `MemoryManager.short.set("goal", goal)` and stores goal_id
4. **Plan generation**: `core/planner.create_plan(...)` produces `{plan_summary, steps}` with ordered `step_index`.
5. **Loop over steps** (`core/orchestrator.py`):
   - apply placeholder substitution into `step["tool_input"]`
   - loop detection: `core/evaluator.check_loop_detection(...)`
   - execute: `core/executor.run_step(step)` → `tools/tool_registry.execute_tool(...)`
   - evaluate: `core/evaluator.evaluate_step(executed)`
   - persist evidence: `state/persistence.save_step(...)`
6. **Terminal**:
   - when tool is `done`, orchestrator calls `core/verifier.verify_goal(...)`
   - only returns success if verifier passes; otherwise triggers `core/planner.replan(...)`.

## Non-Obvious Behaviors & Key Risks (what a developer must know)
- **Decision-making is string-driven.**
  - `core/executor` and `core/evaluator` decide pass/fail based on substrings/prefixes in tool output.
  - A tool wording change can flip statuses without changing intent.
- **Verification is evidence-based but filename inference is heuristic.**
  - `core/verifier.py` extracts filenames from the goal text; if the goal doesn’t contain clear paths/filenames, verification may become weaker or follow the “no filenames” branch.
- **`done` is “trusted only after verifier,” but intermediate steps can still be misclassified.**
  - `evaluate_step` can pass `done`, but orchestrator still requires verifier success.
- **Placeholder substitution injects entire prior results.**
  - `_previous_context()` concatenates *all* step outputs; placeholders can cause massive context bleed into later steps.
- **Replanning rules are not deterministic or tightly constrained.**
  - Orchestrator currently replans on evaluator failure and on verifier failure, bounded by `MAX_RETRIES`, but it does not enforce the stricter deterministic replanning constraints described in your Codex prompt.
- **Tool outputs are currently not structured JSON.**
  - The Codex requirement for structured tool outputs is not implemented in this Phase 1 code; tools return plain strings.

## Suggested Reading Order
1. `main.py`
2. `core/orchestrator.py`
3. `core/planner.py`
4. `tools/tool_registry.py`
5. `core/executor.py` + `core/evaluator.py`
6. `core/verifier.py`
7. `state/persistence.py` + `memory/memory_manager.py`
