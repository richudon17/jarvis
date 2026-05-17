# JARVIS — Codebase Overview (Resource Lifecycle Investigation)

## Summary
This is a small Python autonomous agent (“JARVIS”) that builds plans from a goal, executes tool-based steps, evaluates/verifies results, and persists progress to a local SQLite DB. Phase 2 includes a Playwright-based browser automation layer that returns structured “observation” data for planning/execution feedback. The test suite focuses heavily on robustness and **resource lifecycle correctness** under `pytest -W error` (sqlite connections, Playwright/browser shutdown, and deterministic cleanup on exception paths).

## Architecture
**Primary pattern:** tool-based agent loop with persistence + deterministic browser primitives.
- `core/orchestrator.py` is the top-level control loop:
  1) plan (`core/planner.py`)
  2) execute each step (`core/executor.py` → tool registry)
  3) evaluate (`core/evaluator.py`)
  4) verify/quality/semantic checks (`core/verifier.py`, `core/quality.py`, `core/semantic_verifier.py`)
  5) replan/repair using LLM helpers (`core/planner.py`, `core/deterministic_repair.py`)
  6) persist each step and goal state via `state/persistence.py`.
- Persistence uses direct `sqlite3` calls in `state/persistence.py` and `memory/memory_manager.py`.
- Phase 2 browser actions are implemented in `core/browser.py` using **Playwright sync API**, with a global singleton browser context (`_browser`).

**Execution start:** `main.py` loads env + requires a GROQ API key, then instantiates `core.orchestrator.Orchestrator()` and calls `agent.run(goal)` inside a CLI loop. (Most tests bypass network by patching/guarding tools and using mocked browser/tool calls.)

## Directory Structure
```text
project-root/
├── core/
│   ├── orchestrator.py        — main agent loop (plan → execute → evaluate → verify → persist)
│   ├── planner.py            — LLM plan creation + replan helpers
│   ├── executor.py           — executes a single step via the tool registry
│   ├── evaluator.py          — evaluation/passing logic for executed steps
│   ├── verifier.py           — goal completion verification rules
│   ├── quality.py            — quality gate rules
│   ├── semantic_verifier.py — semantic gate rules
│   ├── deterministic_repair.py — converts failures into retry/convert/stop actions
│   ├── browser.py            — Playwright sync browser primitives + global BrowserContext singleton
│   └── observation.py       — observation schema/history utilities (used by tests)
├── state/
│   └── persistence.py       — sqlite3 persistence for goals/steps
├── memory/
│   └── memory_manager.py   — sqlite3-backed long-term + episodic memory + in-memory short-term
├── tools/
│   └── tool_registry.py     — maps tool names to implementations (file I/O, run_python, web_search, etc.)
├── interface/
│   └── goal_input.py        — CLI input helpers
└── tests/
    ├── test_resource_lifecycle_strict.py — targeted lifecycle tests
    ├── conftest_resource_cleanup.py      — extra defensive GC cleanup fixture
    └── many robustness tests for persistence/tool/verification/browser behavior
```

## Key Abstractions

### Orchestrator
- **File**: `core/orchestrator.py`
- **Responsibility**: Single-threaded “agent brain” that drives plan → step execution → evaluation → deterministic repair → replan/verification and persists every step.
- **Interface**: `Orchestrator.run(goal: str, goal_id: str | None = None) -> str`
- **Lifecycle**: Constructed per CLI session (`main.py`). No explicit global cleanup hook exists in this module.
- **Used by**: `main.py` and tests that exercise end-to-end behavior.

### Persistence (SQLite)
- **File**: `state/persistence.py`
- **Responsibility**: Store goals + steps in SQLite; includes idempotent startup table creation and “reset orphaned running goals”.
- **Interface**:
  - `init_db()`: create tables
  - `save_goal(goal_id, goal_text, status)`
  - `update_goal_status(goal_id, status)`
  - `save_step(...)`
  - `load_goal(goal_id)`
  - `load_steps(goal_id)`
  - `reset_orphaned_goals()`
- **Lifecycle / Concurrency**: Stateless helper functions; each DB operation opens a new connection via `_conn_ctx()` and closes deterministically in `finally`.

### Long-term + episodic memory (SQLite)
- **File**: `memory/memory_manager.py`
- **Responsibility**: Persist “long_term” facts and “episodic” strategy notes to the same SQLite DB (`jarvis_state.db`).
- **Interface**:
  - `MemoryManager.short` (in-memory dict)
  - `MemoryManager.long` (`LongTermMemory`)
  - `MemoryManager.episodic` (`EpisodicMemory`)
- **Lifecycle / Resource lifecycle risk**: The module defines `_get_conn()` and uses `with _get_conn() as conn:`. In CPython, `sqlite3.Connection.__enter__/__exit__` in practice **does not guarantee deterministic close** (it typically commits/rolls back but does not always call `close()` deterministically). This is a key suspect for `ResourceWarning: unclosed database in sqlite3.Connection`.

### BrowserContext singleton (Playwright sync)
- **File**: `core/browser.py`
- **Responsibility**: Provides deterministic “browser primitives” (open/click/type/extract/screenshot/wait/back/forward) with safety limits and structured observations.
- **Interface**:
  - `BrowserContext.start(headless=True) -> dict`
  - `BrowserContext.stop() -> dict` (idempotent defensive cleanup)
  - `browser_open`, `browser_click`, etc.
  - `close_browser()` (wrapper around `_browser.stop()`)
- **Lifecycle / Resource lifecycle risk**:
  - The module allocates a **global singleton**: `_browser = BrowserContext()`.
  - Playwright allocation occurs only in `BrowserContext.start()`.
  - `start()` calls `self.stop()` first, and `stop()` closes `page`, `context`, `browser`, and calls `playwright.stop()` if available, then nulls references.
  - This design is intended to be idempotent on partial startup failure; tests include monkeypatching `sync_playwright().start()` to raise and then calling `_browser.stop()`.

### BrowserStateManager (global safety counters)
- **File**: `core/browser.py`
- **Responsibility**: Tracks navigation depth and action counts to enforce safety caps.
- **Lifecycle**: Global `_state` reset on successful start/stop.

### Resource cleanup test fixtures
- **File**: `tests/conftest_resource_cleanup.py`
- **Responsibility**: After each test, run `gc.collect()` to force CPython finalizers and surface any `ResourceWarning` as failures under strict warning mode.

## Data Flow

1. CLI receives a goal → `main.py`
2. `Orchestrator.run()` persists goal to SQLite (`state/persistence.py`) and sets short-term memory keys (`memory_manager.py`).
3. Plan generation → `core/planner.create_plan()` returns ordered steps.
4. Each step:
   - placeholder resolution in `core/orchestrator.py`
   - execute the step via `core/executor.run_step()` (tool dispatch through `tools/tool_registry.py`)
   - evaluation via `core/evaluator.evaluate_step()`
   - optional deterministic repair in `core/deterministic_repair.py`
   - persistence of the step record in SQLite via `state/persistence.save_step()`
5. Terminal `done` step triggers `verify_goal()` + `review_quality()` + `semantic_verify_goal()` gates.

## Non-Obvious Behaviors & Design Decisions

### 1) SQLite connection lifecycle is not symmetric across persistence and memory
- `state/persistence.py` uses `_conn_ctx()` with an explicit `conn.close()` in `finally`, which is strong deterministic cleanup.
- `memory/memory_manager.py` uses `with _get_conn() as conn:` but `_get_conn()` returns a raw `sqlite3.Connection` without wrapping `close()` in a custom context manager. This is a high-probability root cause for “unclosed database in sqlite3.Connection” warnings that only appear under `pytest -W error`.

**Why it was likely built this way:** persistence likely got explicit cleanup added, while memory was implemented quickly using the built-in context manager pattern. Under strict warning mode, the built-in context manager semantics are insufficient to suppress `ResourceWarning` from not explicitly closing.

### 2) Playwright lifecycle is singleton-driven; tests enforce partial failure cleanup
- `core/browser.py` is explicitly designed to be idempotent in `BrowserContext.stop()`.
- The module calls `self.stop()` at the start of `BrowserContext.start()` to avoid stale partial state.
- Tests in `tests/test_resource_lifecycle_strict.py` cover the “start throws” path by monkeypatching `sync_playwright().start()` and ensuring `_browser.stop()` is safe.

### 3) Import-time side effects are (mostly) avoided, but globals still exist
- `core/browser.py` creates `_browser = BrowserContext()` at import time, but does not allocate external resources until `start()` is called.
- `state/persistence.py` and `memory/memory_manager.py` define module-level `DB_PATH`, but do not open connections at import time.
- Tests include import/reload safety for `state.persistence`.

## Module Reference
| File | Purpose |
|---|---|
| `core/orchestrator.py` | Agent loop: plan → execute → evaluate → repair/replan → verify → persist |
| `state/persistence.py` | SQLite goals/steps persistence with explicit close in context manager |
| `memory/memory_manager.py` | SQLite long-term + episodic memory, in-memory short-term |
| `core/browser.py` | Playwright sync browser primitives with global singleton context |
| `tools/tool_registry.py` | Tool dispatch (file I/O, python execution, web search, summarization, etc.) |
| `tests/test_resource_lifecycle_strict.py` | Strict lifecycle regression tests |

## Suggested Reading Order
1. `core/orchestrator.py` — to understand how the whole system is driven
2. `state/persistence.py` — persistence lifecycle patterns and invariants
3. `memory/memory_manager.py` — where SQLite cleanup diverges from persistence
4. `core/browser.py` — how browser resources are managed under partial failures
5. `tests/test_resource_lifecycle_strict.py` + `tests/conftest_resource_cleanup.py` — what the test suite asserts about cleanup

## Immediate Likely Root Cause(s) (based on code inspection)
1. **SQLite connection leaks in `memory/memory_manager.py`**:
   - Uses `with _get_conn() as conn:` but does not explicitly `conn.close()` in a deterministic custom context manager.
2. **Potential cursor leaks** (less likely with sqlite3 + implicit cursor cleanup), but any helper that returns raw connections or cursors would be suspicious (no obvious raw returns found in inspected files).
3. Browser sockets/loops are less suspicious because `BrowserContext.stop()` is defensive and tests cover partial startup failure; however, developers should still ensure no background Playwright transports persist after exceptions.
