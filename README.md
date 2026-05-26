# Project Overview
# 🤖 AURUM — Autonomous AI Agent

> *"Sometimes you gotta run before you can walk."* — Tony Stark

---

## What is AURUM?

AURUM is an autonomous AI agent built from the ground up — designed to think, plan, and act independently to complete complex tasks with minimal human input. This isn't just another chatbot wrapper. The goal is a system that can reason through problems, break them into steps, use tools, and execute — all on its own.

AURUM is the full system — the agent, the framework, and the pipeline. The repo lives under the name Jarvis, but everything under the hood runs AURUM.

This project is still actively being developed. New capabilities are being added regularly, and the architecture is evolving as the project grows.

---

## 🧠 Core Concept

Most AI tools today are reactive — you ask, they answer. AURUM is being built to be **proactive and autonomous**. Given a high-level goal, it should be able to:

- Break the goal down into actionable sub-tasks
- Decide which tools or resources to use
- Execute those tasks in sequence or in parallel
- Reflect on results and course-correct when needed
- Report back when the job is done

Think less "assistant" and more "agent."

---

## 🚧 Current Status

This project is a **work in progress**. Here's where things stand:

| Feature | Status |
|---|---|
| Core agent loop (orchestrator → planner → executor) | 🟡 In progress |
| Tool registry & extensible tool use | 🟡 In progress |
| Memory & context management | 🟡 In progress |
| Multi-step task planning | 🟡 In progress |
| Evaluation & quality checks | 🟡 In progress |
| Browser automation | 🟡 In progress |
| Web interface | 🟡 In progress |
| Self-reflection / semantic verification | 🟡 In progress |
| Voice interface | 🔴 Planned |

---

## 🛠️ Tech Stack

- **Language:** Python
- **AI Backend:** Groq API
- **Agent Framework:** AURUM (custom-built)
- **Web Interface:** Flask + vanilla JS
- **Persistence:** SQLite (`aurum_state.db`)
- **Testing:** pytest

> Stack may evolve as the project grows.

---

## 🗂️ Project Structure

```
jarvis/
│
├── core/                          # AURUM — the heart of the agent
│   ├── orchestrator.py            # Top-level agent control loop
│   ├── planner.py                 # Goal decomposition & task planning
│   ├── executor.py                # Task execution engine
│   ├── evaluator.py               # Output evaluation & scoring
│   ├── verifier.py                # Result verification
│   ├── semantic_verifier.py       # Semantic correctness checks
│   ├── quality.py                 # Quality assurance layer
│   ├── deterministic_repair.py    # Auto-repair for failed tasks
│   ├── workspace.py               # Per-goal workspace management
│   ├── environment.py             # Runtime environment setup
│   ├── observation.py             # Agent observation handling
│   ├── artifact.py                # Artifact creation & tracking
│   ├── browser.py                 # Browser automation
│   └── smoke_test.py              # Quick sanity checks
│
├── tools/
│   └── tool_registry.py           # Pluggable tool system
│
├── memory/
│   └── memory_manager.py          # Context & memory management
│
├── state/
│   └── persistence.py             # State persistence layer
│
├── interface/
│   └── goal_input.py              # Goal ingestion & parsing
│
├── tests/                         # Full test suite
│   ├── test_phase1_semantics.py
│   ├── test_phase2_browser.py
│   ├── test_phase2_e2e_browser.py
│   ├── test_reliability_stress.py
│   ├── test_decision_flow_stress.py
│   ├── test_execution_trace_stress.py
│   ├── test_resource_lifecycle_strict.py
│   ├── conftest.py
│   └── conftest_resource_cleanup.py
│
├── aurum_workspace/               # Runtime workspaces per goal
│   ├── global/
│   └── <goal_id workspaces>
│
├── web/                           # Web UI
│   ├── app.py                     # Flask app
│   ├── templates/
│   │   └── index.html
│   └── static/
│       ├── app.js
│       └── style.css
│
├── main.py                        # Entry point
├── requirements.txt
├── requirements-dev.txt
├── aurum_state.db                 # Persistent agent state
└── TODO.md
```

---

## 🚀 Getting Started

### Prerequisites

- Python 3.10+
- A [Groq API key](https://console.groq.com)

### Installation

```bash
# Clone the repo
git clone https://github.com/YOURUSERNAME/jarvis.git
cd jarvis

# Install dependencies
pip install -r requirements.txt

# Set up environment variables
cp .env.example .env
# Add your Groq API key to .env
```

### Running AURUM

```bash
python main.py
```

### Running the Web Interface

```bash
python web/app.py
```

### Running Tests

```bash
# Standard tests
pytest tests/

# Dev dependencies required
pip install -r requirements-dev.txt
```

---

## 🔑 Environment Variables

Create a `.env` file in the root directory:

```
GROQ_API_KEY=your_key_here
```

> ⚠️ Never commit your real `.env` file. It's already in `.gitignore`.

---

## 💡 Motivation

I'm a young developer taking on my most ambitious project yet. AURUM started as a curiosity — *what would it actually take to build a proper autonomous agent from scratch?* — and turned into a full-blown obsession.

This is my first time tackling a project of this scale and complexity. No team, no blueprint, just a lot of research, trial and error, and genuine passion for AI. Every commit is a learning experience, and I'm building this in public so others can follow along, contribute, or just see how the sausage gets made.

If you're also figuring things out as you go — you're in good company.

---

## 🤝 Contributing

This is a personal project but I'm open to ideas, suggestions, and contributions. If you spot a bug, have a feature idea, or just want to say what's up — open an issue or a PR.

---

## 📬 Contact

Feel free to reach out via GitHub issues for anything related to the project.

---

## 📄 License

MIT License — do what you want with it, just don't claim it's yours.

---

<p align="center">Built with curiosity, caffeine, and way too many late nights.</p>
