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
(See `project_info__6.md` for the full, detailed technical report.)

## Data Flow
(See `project_info__6.md` for the full, detailed technical report.)

## Non-Obvious Behaviors & Design Decisions
(See `project_info__6.md` for the full, detailed technical report.)

## Suggested Reading Order
1. `main.py`
2. `core/orchestrator.py`
3. `core/planner.py`
4. `tools/tool_registry.py`
5. `core/executor.py` → `core/evaluator.py`
6. `core/verifier.py`
7. `state/persistence.py` and `memory/memory_manager.py`