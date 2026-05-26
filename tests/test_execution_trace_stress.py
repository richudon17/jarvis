from __future__ import annotations

import json
import random
import uuid
from pathlib import Path

import pytest

import memory.memory_manager as memory_manager
import state.persistence as persistence
from core.orchestrator import Orchestrator
from core.workspace import (
    clear_execution_context,
    goal_workspace_dir,
    resolve_workspace_path,
    set_execution_context,
    workspace_root,
)
from tools import tool_registry


@pytest.fixture
def isolated_db(tmp_path, monkeypatch):
    db = tmp_path / "aurum_trace_state.db"
    monkeypatch.setattr(persistence, "DB_PATH", str(db))
    monkeypatch.setattr(memory_manager, "DB_PATH", str(db))
    persistence.init_db()
    return db


def _read_trace(goal_id: str) -> dict:
    trace_path = goal_workspace_dir(goal_id) / "execution_trace.json"
    assert trace_path.exists(), f"missing trace file at {trace_path}"
    return json.loads(trace_path.read_text(encoding="utf-8"))


def _event_types(trace: dict) -> list[str]:
    return [event.get("event_type") for event in trace.get("timeline", [])]


def test_full_execution_trace_validation_randomized(monkeypatch, isolated_db):
    random.seed(42)
    filenames = [f"g{i}.txt" for i in range(10)]

    def fake_plan(goal, memory=None):
        fname = random.choice(filenames)
        return {
            "plan_summary": "randomized plan",
            "steps": [
                {"step_index": 0, "description": "write file", "tool": "file_write", "tool_input": {"path": fname, "content": goal}},
                {"step_index": 1, "description": "read file", "tool": "file_read", "tool_input": {"path": fname}},
                {"step_index": 2, "description": "done", "tool": "done", "tool_input": {"summary": "ok"}},
            ],
        }

    monkeypatch.setattr("core.orchestrator.create_plan", fake_plan)
    monkeypatch.setattr("core.orchestrator.replan", lambda *args, **kwargs: {"steps": []})

    orch = Orchestrator()
    for idx in range(6):
        gid = f"trace-random-{idx}"
        result = orch.run(f"random goal {idx}", goal_id=gid)
        assert result.startswith("completed:")

        trace = _read_trace(gid)
        events = trace.get("timeline", [])
        types = _event_types(trace)
        assert trace.get("final_status") == "completed"
        assert len(events) > 0
        assert types.count("step_started") >= 3
        assert types.count("step_finished") >= 3
        assert "quality_decision" in types
        assert "goal_completed" in types

        # Every step_finished must include trace-required fields.
        for ev in events:
            if ev.get("event_type") == "step_finished":
                assert "step_index" in ev
                assert "tool" in ev
                assert "input" in ev
                assert "output" in ev
                assert "status" in ev
                assert "timestamp" in ev


def test_chaotic_file_operations_and_isolation():
    goal_id = f"trace-chaos-{uuid.uuid4().hex[:6]}"
    leak_abs = Path("/tmp/aurum_abs_leak.txt")
    if leak_abs.exists():
        leak_abs.unlink()
    set_execution_context(goal_id, "chaotic file ops")
    try:
        # direct tool stress with valid + invalid paths
        valid = tool_registry.file_write(path="chaos/file.txt", content="v1")
        overwrite = tool_registry.file_write(path="chaos/file.txt", content="v2")
        invalid_abs = tool_registry.file_write(path="/tmp/aurum_abs_leak.txt", content="leak")
        invalid_rel = tool_registry.file_write(path="../aurum_rel_leak.txt", content="leak")

        assert valid["ok"] is True
        assert overwrite["ok"] is True
        assert invalid_abs["ok"] is False
        assert invalid_rel["ok"] is False

        p = Path(valid["data"]["path"])
        assert p.exists()
        p.unlink()
        recreated = tool_registry.file_write(path="chaos/file.txt", content="v3")
        assert recreated["ok"] is True

        read = tool_registry.file_read(path="chaos/file.txt")
        assert read["ok"] is True
        assert read["data"]["content"] == "v3"
        assert str(Path(recreated["data"]["path"]).resolve()).startswith(str(workspace_root().resolve()))
        assert str(Path(recreated["data"]["path"]).resolve()).startswith(str(goal_workspace_dir(goal_id)))
        assert not leak_abs.exists()
    finally:
        clear_execution_context()


def test_repair_chaos_trace_visibility(monkeypatch, isolated_db):
    def bad_plan(goal, memory=None):
        return {
            "plan_summary": "bad repair plan",
            "steps": [
                {"step_index": 0, "description": "bad write", "tool": "file_write", "tool_input": {"path": "../oops.txt", "content": "x"}},
                {"step_index": 1, "description": "done", "tool": "done", "tool_input": {"summary": "done"}},
            ],
        }

    monkeypatch.setattr("core.orchestrator.create_plan", bad_plan)
    monkeypatch.setattr("core.orchestrator.replan", lambda *args, **kwargs: bad_plan("", None))

    gid = "trace-repair-chaos"
    result = Orchestrator().run("repair chaos", goal_id=gid)
    assert result.startswith("failed:")

    trace = _read_trace(gid)
    types = _event_types(trace)
    assert "deterministic_repair_triggered" in types
    assert trace.get("final_status") == "failed"
    assert len(trace.get("timeline", [])) < 200  # bounded; no infinite loops


def test_planner_chaos_trace_reasoning_gap(monkeypatch, isolated_db):
    # Ambiguous plan: immediately done with no evidence.
    monkeypatch.setattr(
        "core.orchestrator.create_plan",
        lambda goal, memory=None: {
            "plan_summary": "ambiguous fast done",
            "steps": [
                {"step_index": 0, "description": "done", "tool": "done", "tool_input": {"summary": "ambiguous"}},
            ],
        },
    )
    monkeypatch.setattr("core.orchestrator.replan", lambda *args, **kwargs: {"steps": []})

    gid = "trace-planner-chaos"
    result = Orchestrator().run("Do something ambiguous", goal_id=gid)
    assert result.startswith("failed:")

    trace = _read_trace(gid)
    quality_events = [e for e in trace.get("timeline", []) if e.get("event_type") == "quality_decision"]
    assert quality_events, "quality decision trace missing"
    quality = quality_events[-1]
    assert quality.get("passed") is False
    assert isinstance(quality.get("reasoning_trace"), list)
    assert isinstance(quality.get("failure_factors"), list)
    assert quality.get("decision_reason")


def test_quality_gate_stress_trace_explanations(monkeypatch, isolated_db):
    def weak_code_plan(goal, memory=None):
        return {
            "plan_summary": "weak code",
            "steps": [
                {"step_index": 0, "description": "write weak code", "tool": "file_write", "tool_input": {"path": "weak.py", "content": "def main():\n    pass\n"}},
                {"step_index": 1, "description": "done", "tool": "done", "tool_input": {"summary": "done"}},
            ],
        }

    monkeypatch.setattr("core.orchestrator.create_plan", weak_code_plan)
    monkeypatch.setattr("core.orchestrator.replan", lambda *args, **kwargs: {"steps": []})

    gid = "trace-quality-stress"
    result = Orchestrator().run("Write weak.py", goal_id=gid)
    assert result.startswith("failed:")

    trace = _read_trace(gid)
    quality_events = [e for e in trace.get("timeline", []) if e.get("event_type") == "quality_decision"]
    assert quality_events
    q = quality_events[-1]
    assert q.get("passed") is False
    assert q.get("failure_factors")
    assert any("placeholder" in str(f.get("factor", "")).lower() or "placeholder" in str(f.get("detail", "")).lower() for f in q.get("failure_factors", []))


def test_concurrent_execution_trace_separation(monkeypatch, isolated_db):
    def per_goal_plan(goal, memory=None):
        token = goal.split()[-1]
        return {
            "plan_summary": "per-goal file",
            "steps": [
                {"step_index": 0, "description": "write", "tool": "file_write", "tool_input": {"path": f"{token}.txt", "content": token}},
                {"step_index": 1, "description": "done", "tool": "done", "tool_input": {"summary": token}},
            ],
        }

    monkeypatch.setattr("core.orchestrator.create_plan", per_goal_plan)
    monkeypatch.setattr("core.orchestrator.replan", lambda *args, **kwargs: {"steps": []})

    orch = Orchestrator()
    goal_ids = []
    for i in range(4):
        gid = f"trace-concurrent-{i}-{uuid.uuid4().hex[:4]}"
        goal_ids.append(gid)
        result = orch.run(f"goal token_{i}", goal_id=gid)
        assert result.startswith("completed:")

    seen_paths = set()
    for gid in goal_ids:
        trace = _read_trace(gid)
        assert trace.get("goal_id") == gid
        trace_file = str(goal_workspace_dir(gid) / "execution_trace.json")
        assert trace_file not in seen_paths
        seen_paths.add(trace_file)

        # Ensure artifacts stay in each goal workspace.
        step_finishes = [e for e in trace.get("timeline", []) if e.get("event_type") == "step_finished"]
        for ev in step_finishes:
            output = ev.get("output") or {}
            data = output.get("data") or {}
            path = data.get("path")
            if path:
                assert str(Path(path).resolve()).startswith(str(goal_workspace_dir(gid)))


def test_loop_detection_visibility_in_trace(monkeypatch, isolated_db):
    repeating = {"step_index": 0, "description": "repeat fail", "tool": "file_write", "tool_input": {"path": "../repeat.txt", "content": "x"}}

    monkeypatch.setattr(
        "core.orchestrator.create_plan",
        lambda goal, memory=None: {"plan_summary": "loop", "steps": [repeating.copy(), repeating.copy(), repeating.copy(), repeating.copy()]},
    )
    monkeypatch.setattr("core.orchestrator.MAX_REPLAN_ATTEMPTS", 10)
    monkeypatch.setattr("core.orchestrator.replan", lambda *args, **kwargs: {"steps": [repeating.copy(), repeating.copy(), repeating.copy()]})
    monkeypatch.setattr("core.orchestrator.deterministic_repair", lambda *args, **kwargs: {"handled": False})

    gid = "trace-loop-visible"
    result = Orchestrator().run("force loop", goal_id=gid)
    assert result.startswith("failed:")

    trace = _read_trace(gid)
    types = _event_types(trace)
    assert "loop_detection_triggered" in types
    assert trace.get("final_status") == "failed"
