# AURUM — Codebase Overview (Phase 1)

## Summary
AURUM is a Python-based “goal → plan → execute → evaluate → (replan) → verify → finish” autonomous agent that persists goals and step history in SQLite so it can resume/inspect prior runs. It uses an LLM (Groq) to generate tool-based step plans and a simple heuristic evaluator + rule-based verifier to decide whether a terminal “done” step is trustworthy. Tools are implemented in `tools/tool_registry.py` (web search via DuckDuckGo, file read/write, and Python execution via a subprocess), and the orchestrator stitches everything together in a bounded loop (max steps, max retries).

## Architecture
**Primary pattern:** orchestration loop with planner/executor/evaluator/verifier pipeline (layered, but “agent loop” at the center).

**Technology stack:**
- **Language/Runtime:** Python 3
- **LLM integration:** `groq` SDK (`core/planner.py` uses `Groq(...)`)
- **CLI UI:** `rich` (panels, console printing, tables)
- **State persistence:** SQLite via Python `sqlite3` (`state/persistence.py`, shared DB file `aurum_state.db`)
- **Web search:** `ddgs` (DuckDuckGo HTML-less aggregator)
- **Environment config:** `python-dotenv` (`main.py`)

**Execution start / entry point:**
- `main.py` loads `.env`, validates `GROQ_API_KEY`, then enters a CLI loop:
  1. `prompt_goal()` in `interface/goal_input.py`
  2. `Orchestrator().run(goal)` in `core/orchestrator.py`

## Directory Structure
```
project-root/
├── main.py                    — CLI entry point; loads env; creates Orchestrator
├── core/                      — Agent internals (planner/executor/evaluator/verifier)
│   ├── orchestrator.py        — Central control loop + placeholder resolution + persistence
│   ├── planner.py            — LLM-based step planning + replanning
│   ├── executor.py           — Tool execution engine
│   ├── evaluator.py          — Result evaluation + loop detection
│   └── verifier.py           — Goal-level rule-based verification
├── tools/                     — Tool implementations and registry
│   └── tool_registry.py      — web_search, summarize_text, file_read/write/list, run_python
├── memory/                    — Memory layers backed by SQLite
│   └── memory_manager.py    — short-term in-memory; long-term/episodic in SQLite
├── state/                     — Persistence for goals/steps
│   └── persistence.py       — SQLite schema + CRUD for goals and steps
├── interface/                 — CLI interface helpers
│   └── goal_input.py       — prompt_goal + show_history
└── aurum_state.db           — SQLite DB (goals/steps/memory)
```

## Key Abstractions

### Orchestrator
- **File**: `core/orchestrator.py` (class `Orchestrator`)
- **Responsibility**: Runs the agent loop end-to-end: create plan, iterate steps, execute tools, evaluate step outcomes, persist step evidence, replan on failure, and verify completion.
- **Interface**:
  - `run(goal: str, goal_id: str | None = None) -> str`: main loop; returns an LLM-generated completion summary.
- **Lifecycle**:
  - Constructed in `main.py` per CLI usage.
  - On init: `reset_orphaned_goals()`, `init_db()`, and creates `MemoryManager()`.
- **Used by**: `main.py` for goal execution; depends on `core.planner`, `core.executor`, `core.evaluator`, `core.verifier`, and `state.persistence`.

### Planner (create_plan / replan)
- **File**: `core/planner.py`
- **Responsibility**: Produces structured plans (JSON) for tools to execute; also produces revised remaining steps after a failure.
- **Interface**:
  - `create_plan(goal, context="", memory=None) -> dict`: returns `{plan_summary, steps}`.
  - `replan(goal, completed_steps, failed_step, failure_reason, memory=None) -> dict`
- **Design meaning**: Plans are “tool graphs” represented as an ordered list of tool invocations, and the orchestrator is responsible for substituting placeholders in later steps based on earlier tool outputs.

### Executor (run_step)
- **File**: `core/executor.py` (`run_step`)
- **Responsibility**: Executes a single plan step by invoking a tool from the registry. Special-cases `tool == "done"` as a terminal action.
- **Interface**:
  - `run_step(step: dict) -> dict`: returns the step dict augmented with `result` and `status`.
- **Key behavior**: Status is derived heuristically from the returned string (`result_lower`) using failure keyword/prefix matching—this is critical because evaluation and later replanning depend on `status`.

### Evaluator (evaluate_step)
- **File**: `core/evaluator.py` (`evaluate_step`)
- **Responsibility**: Per-step “passed/failed” judgement used by the orchestrator to decide whether to replan.
- **Interface**:
  - `evaluate_step(step: dict) -> dict` adds `evaluation: {passed, reason}`
  - `check_loop_detection(step_history, current_step, threshold=3) -> bool`
- **Key behavior**: Uses string keyword heuristics and treats a terminal `done` step as always passed at this layer.

### Verifier (verify_goal)
- **File**: `core/verifier.py` (`verify_goal`)
- **Responsibility**: Final gate before the orchestrator accepts the run as complete when the step tool status is `"done"`.
- **Interface**:
  - `verify_goal(goal: str, completed_steps: list[dict], attempted_steps: list[dict] | None) -> dict`
  - Returns `{passed: bool, reason: str, confidence: float}`
- **Key behavior**: Rule-based verification checks:
  - some successful steps exist before done
  - expected filenames inferred from the goal exist and are non-empty
  - Python files have valid syntax (AST parse)
  - research/search goals include a successful `web_search`
  - summaries aren’t just raw snippets (marker/length checks)
  - calculation goals appear to have an executed tool or substantial output

### Tool Registry
- **File**: `tools/tool_registry.py`
- **Responsibility**: Implements and registers all tools available to the planner:
  - `web_search` (DDGS)
  - `summarize_text`
  - `file_read`, `file_write`, `file_list`
  - `run_python` (subprocess + basic blocking heuristics)
- **Interface**:
  - `get_tool_descriptions() -> str` for planner prompt injection
  - `execute_tool(tool_name: str, params: dict) -> str` returns tool result as a string

### Memory Manager
- **File**: `memory/memory_manager.py`
- **Responsibility**: Three-layer memory system:
  - **Short-term**: in-memory dict for current task session
  - **Long-term**: SQLite key/value facts
  - **Episodic**: SQLite history of past task histories and strategies
- **Interface**:
  - `ShortTermMemory.set/get/snapshot`
  - `LongTermMemory.set/get/all`
  - `EpisodicMemory.record/recall_recent`

## Data Flow (Concrete)
1. User enters goal via `interface/goal_input.prompt_goal()` (CLI input).
2. `main.py` calls `Orchestrator.run(goal)`.
3. Orchestrator persists goal: `state.persistence.save_goal(...)`; sets short-term memory `MemoryManager.short`.
4. Orchestrator requests plan: `core.planner.create_plan(goal, memory=...)`.
   - Planner calls Groq and requires a final `done` step.
5. Orchestrator iterates steps (bounded by `MAX_STEPS`):
   - resolves placeholders in `tool_input` using `_resolve_step_placeholders(...)`
   - checks loop detection via `core.evaluator.check_loop_detection(...)`
   - executes via `core.executor.run_step(...)` → `tools.tool_registry.execute_tool(...)`
6. After execution:
   - evaluates via `core.evaluator.evaluate_step(executed)`
   - persists step evidence via `state.persistence.save_step(...)`
7. If terminal `done` status:
   - runs `core.verifier.verify_goal(...)` before declaring completion.
8. If successful:
   - generates a completion summary via Groq and records episodic memory.

## Non-Obvious Behaviors & Design Decisions
- **“done” is not trusted by the system until verifier passes.**
  Even though `evaluate_step` passes `done`, the orchestrator still calls `verify_goal()` and only returns success if it passes.
- **Placeholder system is string-based and concatenates full prior results.**
  `_previous_context()` joins all previous step outputs; placeholder substitution can therefore inject large amounts of earlier content.
- **Tool success/failure is inferred from unstructured string results.**
  `executor.run_step` and `evaluate_step` both use substring/prefix heuristics, which are sensitive to wording changes.
- **Planner enforces a terminal `done` step but replanning can replace remaining steps.**
  The orchestrator can discard remaining steps if replanning is triggered.
- **GUI/game-like goals trigger template-based code fallback planning.**
- **Heuristic memory injection exists in the planner, with potential key-mismatch risk.**
  Planner code assumes certain episodic record shapes/keys when constructing “episodes_text”.

## Suggested Reading Order
1. `main.py`
2. `core/orchestrator.py`
3. `core/planner.py`
4. `tools/tool_registry.py`
5. `core/executor.py` and `core/evaluator.py`
6. `core/verifier.py`
7. `state/persistence.py` and `memory/memory_manager.py`