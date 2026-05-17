# JARVIS — Codebase Overview (Phase 1)

## Summary
JARVIS is a Python-based “goal → plan → execute → evaluate → (replan) → verify → finish” autonomous agent that persists goals and step history in SQLite so it can resume/inspect prior runs. It uses an LLM (Groq) to generate tool-based step plans and a simple heuristic evaluator + rule-based verifier to decide whether a terminal “done” step is trustworthy. Tools are implemented in `tools/tool_registry.py` (web search via DuckDuckGo, file read/write, and Python execution via a subprocess), and the orchestrator stitches everything together in a bounded loop (max steps, max retries).

## Architecture
**Primary pattern:** orchestration loop with planner/executor/evaluator/verifier pipeline (layered, but “agent loop” at the center).

**Technology stack:**
- **Language/Runtime:** Python 3
- **LLM integration:** `groq` SDK (`core/planner.py` uses `Groq(...)`)
- **CLI UI:** `rich` (panels, console printing, tables)
- **State persistence:** SQLite via Python `sqlite3` (`state/persistence.py`, shared DB file `jarvis_state.db`)
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
│   ├── planner.py            — LLM prompt + plan JSON generation + replanning
│   ├── executor.py           — Executes tool calls from a plan (no extra reasoning)
│   ├── evaluator.py          — Heuristic per-step pass/fail (based on result text + status)
│   └── verifier.py           — Goal-level rule-based verification when tool says "done"
├── tools/                     — Tool implementations and registry
│   └── tool_registry.py      — web_search, summarize_text, file_read/write/list, run_python
├── memory/                   — Memory layers backed by SQLite
│   └── memory_manager.py    — short-term in-memory; long-term/episodic in SQLite
├── state/                    — Persistence for goals/steps and resuming/inspection
│   └── persistence.py       — SQLite schema + CRUD for goals and steps
├── interface/                — CLI interface helpers
│   └── goal_input.py       — prompt_goal + show_history
└── jarvis_state.db          — SQLite DB (goals/steps/memory)
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
  - `_call_llm(...)`: Groq call that expects JSON object responses.
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
- **Key behavior**: Uses string keyword heuristics (“success keywords”, “failure phrases”, minimum output length) and treats a terminal `done` step as always passed at this layer.

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
  - summaries aren’t just raw snippets (looks for marker counts and length)
  - calculation goals appear to have an executed tool or substantial output

### Tool Registry
- **File**: `tools/tool_registry.py`
- **Responsibility**: Implements and registers all tools available to the planner:
  - `web_search(query, max_results)` → DDGS text snippets
  - `summarize_text(text, goal, max_items)` → Markdown summary with sources
  - `file_read(path)`, `file_write(path, content)`, `file_list(directory)`
  - `run_python(code)` → subprocess execution with basic GUI/interactive blocking
- **Interfaces**:
  - `get_tool_descriptions() -> str`: produces planner prompt tool list
  - `execute_tool(tool_name: str, params: dict) -> str`: calls the registered function

### Memory Manager
- **File**: `memory/memory_manager.py`
- **Responsibility**: Three-layer memory system:
  - `ShortTermMemory`: in-memory dict for current goal context
  - `LongTermMemory`: SQLite key/value facts
  - `EpisodicMemory`: SQLite history of previous goals/outcomes/strategy notes
- **Interface**:
  - `ShortTermMemory.set/get/snapshot`
  - `LongTermMemory.set/get/all`
  - `EpisodicMemory.record/recall_recent`
- **Important current coupling**: `core/planner.py` expects `memory.episodic.recall_recent(...)` entries and tries to access fields like `strategy_notes` (but record format stored is `outcome` and `strategy_notes`—the planner also assumes keys `goal` / `outcome` / `strategy_notes`, which is a potential mismatch depending on how rows are serialized).

## Data Flow (Concrete)
1. User enters goal via `interface/goal_input.prompt_goal()` (CLI input).
2. `main.py` calls `Orchestrator.run(goal)`.
3. Orchestrator:
   - persists goal: `state.persistence.save_goal(...)`
   - sets short-term memory: `MemoryManager.short.set(...)`
4. Orchestrator requests plan: `core.planner.create_plan(goal, memory=...)`.
   - Planner calls Groq (`_call_llm`) with a system prompt instructing **steps must be tool calls** and includes a final `done` step.
5. Orchestrator iterates steps (bounded by `MAX_STEPS`):
   - resolves placeholders in `tool_input` using `_resolve_step_placeholders(...)`:
     - substitutes placeholder-only strings like `{search_results}` with concatenated previous step results
     - special-case for `file_write` `.py` content written after `summarize_text`
   - loop detection: `core.evaluator.check_loop_detection(...)`
   - executes: `core.executor.run_step(step)` → `tools.tool_registry.execute_tool(...)`
6. After execution, Orchestrator:
   - evaluates: `core.evaluator.evaluate_step(executed)`
   - persists step evidence: `state.persistence.save_step(...)`
   - if terminal `done` status: run goal-level verification: `core.verifier.verify_goal(...)`
7. If step fails or verification fails, Orchestrator replans via `core.planner.replan(...)` and continues (bounded by `MAX_RETRIES`).
8. If successful, Orchestrator generates a completion summary via `_call_llm(...)` and records episodic memory.

## Non-Obvious Behaviors & Design Decisions
- **“done” is not trusted by the system until verifier passes.**  
  At the orchestration level, `evaluate_step` returns pass for `done`, but `Orchestrator` still performs `verify_goal()` and only returns success if `verification["passed"]` is true.
- **Placeholder system is string-based and context is concatenated.**  
  `_previous_context()` joins *all* previous step results in order. This means later tools can be fed very large context and the placeholder substitution does not selectively extract just “search results”; it injects prior step outputs wholesale.
- **Tool success/failure is inferred from tool result text, not from structured return values.**  
  `executor.run_step` sets `status` using substring/prefix heuristics over `result` (string). Then `evaluate_step` also uses string heuristics. This makes behavior “brittle” to wording changes.
- **Planner enforces a terminal tool step, but there is also an internal replanning system.**  
  Even though the plan must end in `done`, the orchestrator can discard remaining steps and replace them using `replan()` if a step fails or verification fails.
- **Planner has explicit “code fallback plans” for GUI/game-like requests.**  
  `planner.create_plan` checks for GUI/game keywords or `.py` code generation goals and then uses templates (`TODO_TEMPLATE`, `TETRIS_TEMPLATE`) or an LLM raw-code request wrapped into a `file_write` + `done` plan.
- **State persistence is append-only for steps but goals are overwritten by status.**  
  Goals and steps are stored in SQLite, but the orchestrator does not implement a true “resume from last unfinished step” path; it uses persistence mainly for inspection and cleanup semantics.
- **Safety constraints exist, but they’re heuristic.**  
  `run_python` blocks interactive/GUI-ish code by searching for keywords like `input(` or pygame imports, but this is not a sandbox beyond subprocess timeout and keyword filtering.

## Suggested Reading Order
1. `main.py` — see the CLI lifecycle and how Orchestrator is invoked.
2. `core/orchestrator.py` — understand the main loop, placeholder injection, retry/replan semantics, and verification gating.
3. `core/planner.py` — study how plans are generated and why the planner’s prompt hard-codes tool JSON shapes and a terminal `done`.
4. `tools/tool_registry.py` — enumerate concrete tool behaviors and returned string formats that feed evaluator/verifier.
5. `core/executor.py` + `core/evaluator.py` — understand how “pass/fail” is inferred from unstructured tool output strings.
6. `core/verifier.py` — understand final goal-level checks that prevent fake completion.
7. `state/persistence.py` and `memory/memory_manager.py` — understand what is persisted and what memory is used for planning.
