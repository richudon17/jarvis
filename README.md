# JARVIS — Persistent Goal-Driven Autonomous Agent

A free, fully autonomous AI agent that accepts high-level goals, plans steps, executes them using tools, and recovers from failures — all with persistent state across sessions.

---

## Phase 1 Features
- ✅ Goal → Plan → Execute → Evaluate loop
- ✅ Web search (DuckDuckGo, no API key needed)
- ✅ File read/write
- ✅ Python code execution
- ✅ 3-layer memory (short-term, long-term, episodic)
- ✅ SQLite state persistence (resume after interruption)
- ✅ Automatic replanning on failure (up to 3 retries)
- ✅ Loop detection
- ✅ Powered by Gemini 2.0 Flash (free tier)

---

## Setup

### 1. Get an API key
You can use either:
- `GEMINI_API_KEY` for Gemini
- `GROQ_API_KEY` for Groq

### 2. Install dependencies
```bash
python3 -m pip install -r requirements.txt
```

### 3. Configure environment
```bash
cp .env.example .env
# Edit .env and paste your GEMINI_API_KEY or GROQ_API_KEY
```

### 4. Run JARVIS
```bash
python3 main.py
```

---

## Example Goals
```
Goal > Research the latest developments in fusion energy and save a summary to fusion_summary.txt
Goal > Write a Python script that generates the first 100 prime numbers and run it
Goal > Search for the best free Python libraries for data visualization and list the top 5
```

---

## Project Structure
```
jarvis/
├── core/
│   ├── orchestrator.py     # Central control loop
│   ├── planner.py          # LLM-based step planning
│   ├── executor.py         # Tool execution engine
│   └── evaluator.py        # Result evaluation + loop detection
├── memory/
│   └── memory_manager.py   # Short/long/episodic memory
├── tools/
│   └── tool_registry.py    # All available tools
├── state/
│   └── persistence.py      # SQLite state management
├── interface/
│   └── goal_input.py       # CLI interface
├── main.py                 # Entry point
├── requirements.txt
└── .env.example
```

---

## Roadmap
- **Phase 2**: Browser automation with Playwright
- **Phase 3**: Persistent re-planning across sessions
- **Phase 4**: Full autonomy mode + multi-step real-world tasks
