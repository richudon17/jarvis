import os
from pathlib import Path

import state.persistence as persistence
from core.orchestrator import Orchestrator


def test_orchestrator_end_to_end_smoke_and_persistence(tmp_path, monkeypatch):
    # Use a temporary DB for isolation
    tmp_db = tmp_path / "state.db"
    monkeypatch.setattr(persistence, "DB_PATH", str(tmp_db))
    persistence.init_db()

    # Prepare deterministic plan: write a python file then done
    file_path = tmp_path / "artifact.py"
    def fake_create_plan(goal, memory=None):
        return {
            "plan_summary": "Write artifact and finish",
            "steps": [
                {
                    "step_index": 1,
                    "description": "Write python artifact",
                    "tool": "file_write",
                    "tool_input": {"path": str(file_path), "content": 'print("hello")\n'},
                },
                {
                    "step_index": 2,
                    "description": "Finish",
                    "tool": "done",
                    "tool_input": {"summary": "done"},
                }
            ]
        }

    # Orchestrator imports create_plan at module import time; patch there.
    monkeypatch.setattr("core.orchestrator.create_plan", fake_create_plan)

    # Monkeypatch tool execution to actually write the file for file_write
    def fake_execute_tool(tool, tool_input):
        if tool == "file_write":
            path = Path(tool_input.get("path"))
            path.parent.mkdir(parents=True, exist_ok=True)
            content = tool_input.get("content", "") or ""
            path.write_text(content)
            return {"ok": True, "data": None, "error": None, "metadata": {"bytes_written": path.stat().st_size}}
        if tool == "done":
            return {"ok": True, "data": {"summary": tool_input.get("summary")}, "error": None, "metadata": {"done": True}}
        return {"ok": False, "data": None, "error": "unknown tool", "metadata": {}}

    monkeypatch.setattr("tools.tool_registry.execute_tool", fake_execute_tool)

    orch = Orchestrator()
    gid = "test-e2e-1"
    summary = orch.run("Create artifact and finish", goal_id=gid)

    # Verify persistence
    g = persistence.load_goal(gid)
    assert g is not None and g.get("status") == "completed"

    steps = persistence.load_steps(gid)
    assert len(steps) >= 2

    # First step should have smoke_test metadata attached in result->metadata
    first = steps[0]
    res = first.get("result") or {}
    meta = (res.get("metadata") or {})
    smoke = meta.get("smoke_test")
    assert isinstance(smoke, dict)
    assert smoke.get("compiled") is True