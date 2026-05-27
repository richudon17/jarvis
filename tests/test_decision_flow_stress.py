from __future__ import annotations

import uuid
from pathlib import Path

import pytest

import memory.memory_manager as memory_manager
import state.persistence as persistence
from core.deterministic_repair import deterministic_repair
from core.evaluator import check_loop_detection, evaluate_step
from core.orchestrator import Orchestrator
from core.quality import review_quality
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
    db = tmp_path / "aurum_test_state.db"
    monkeypatch.setattr(persistence, "DB_PATH", str(db))
    monkeypatch.setattr(memory_manager, "DB_PATH", str(db))
    persistence.init_db()
    return db


@pytest.fixture
def workspace_ctx():
    goal_id = f"stress-{uuid.uuid4().hex[:8]}"
    set_execution_context(goal_id, "stress test")
    try:
        yield goal_id, goal_workspace_dir(goal_id)
    finally:
        clear_execution_context()


def _step(index: int, tool: str, tool_input: dict) -> dict:
    return {
        "step_index": index,
        "description": f"{tool} step {index}",
        "tool": tool,
        "tool_input": tool_input,
    }


def test_quality_is_only_decision_authority(monkeypatch, isolated_db):
    def fake_plan(goal, memory=None):
        return {
            "plan_summary": "minimal",
            "steps": [
                _step(0, "file_write", {"path": "artifact.txt", "content": "ok"}),
                _step(1, "done", {"summary": "done"}),
            ],
        }

    monkeypatch.setattr("core.orchestrator.create_plan", fake_plan)
    monkeypatch.setattr("core.orchestrator.replan", lambda *args, **kwargs: {"steps": []})

    monkeypatch.setattr(
        "core.orchestrator.verify_goal",
        lambda *args, **kwargs: {"advisory_status": "warn", "hints": ["bad"], "confidence": 0.1},
    )
    monkeypatch.setattr(
        "core.orchestrator.semantic_verify_goal",
        lambda *args, **kwargs: {"advisory_status": "warn", "hints": ["bad"], "confidence": 0.1},
    )
    monkeypatch.setattr(
        "core.orchestrator.review_quality",
        lambda *args, **kwargs: {"passed": True, "score": 1.0, "issues": [], "category": "file"},
    )

    result = Orchestrator().run("Create artifact")
    assert result.startswith("completed:")

    monkeypatch.setattr(
        "core.orchestrator.verify_goal",
        lambda *args, **kwargs: {"advisory_status": "ok", "hints": [], "confidence": 0.95},
    )
    monkeypatch.setattr(
        "core.orchestrator.semantic_verify_goal",
        lambda *args, **kwargs: {"advisory_status": "ok", "hints": [], "confidence": 0.95},
    )
    monkeypatch.setattr(
        "core.orchestrator.review_quality",
        lambda *args, **kwargs: {"passed": False, "score": 0.0, "issues": ["nope"], "category": "file"},
    )

    result2 = Orchestrator().run("Create artifact")
    assert result2.startswith("failed:")


def test_file_lifecycle_stress(workspace_ctx):
    goal_id, goal_dir = workspace_ctx
    path = "lifecycle.txt"

    created = tool_registry.file_write(path=path, content="v1")
    assert created["ok"] is True

    overwritten = tool_registry.file_write(path=path, content="v2")
    assert overwritten["ok"] is True

    physical_path = resolve_workspace_path(path, goal_id=goal_id)
    physical_path.unlink()
    assert not physical_path.exists()

    recreated = tool_registry.file_write(path=path, content="v3")
    assert recreated["ok"] is True

    read = tool_registry.file_read(path=path)
    assert read["ok"] is True
    assert read["data"]["content"] == "v3"
    assert physical_path.exists()
    assert str(physical_path).startswith(str(goal_dir))


def test_missing_file_recovery_convert_then_continue(workspace_ctx):
    step = _step(0, "file_read", {"path": "missing.txt"})
    executed = {
        **step,
        "status": "failed",
        "result": {"ok": False, "data": None, "error": "file not found", "metadata": {}},
    }

    repair = deterministic_repair(
        step=step,
        executed_step=executed,
        goal="Create and read missing.txt",
        completed_steps=[],
        attempted_steps=[],
    )
    assert repair["handled"] is True
    assert repair["action"] == "convert"
    assert repair["new_steps"][0]["tool"] == "file_write"

    repaired_step = repair["new_steps"][0]
    repaired_step["tool_input"]["content"] = "recovered content"
    write_result = tool_registry.file_write(**repaired_step["tool_input"])
    assert write_result["ok"] is True

    read_back = tool_registry.file_read(path="missing.txt")
    assert read_back["ok"] is True
    assert "recovered" in read_back["data"]["content"]


def test_multi_step_chaining_10_steps(monkeypatch, isolated_db):
    def fake_plan(goal, memory=None):
        return {
            "plan_summary": "chain plan",
            "steps": [
                _step(0, "file_write", {"path": "a.txt", "content": "alpha"}),
                _step(1, "file_read", {"path": "a.txt"}),
                _step(2, "run_python", {"code": "print('BETA')\n"}),
                _step(3, "file_write", {"path": "b.txt", "content": "beta"}),
                _step(4, "file_read", {"path": "b.txt"}),
                _step(5, "run_python", {"code": "print('GAMMA')\n"}),
                _step(6, "file_write", {"path": "c.txt", "content": "gamma"}),
                _step(7, "file_read", {"path": "c.txt"}),
                _step(8, "file_write", {"path": "final.txt", "content": "done"}),
                _step(9, "done", {"summary": "chain complete"}),
            ],
        }

    monkeypatch.setattr("core.orchestrator.create_plan", fake_plan)
    monkeypatch.setattr("core.orchestrator.replan", lambda *args, **kwargs: {"steps": []})

    orch = Orchestrator()
    gid = "chain-10"
    result = orch.run("Create final.txt after a long chain", goal_id=gid)
    assert result.startswith("completed:")

    final_path = resolve_workspace_path("final.txt", goal_id=gid)
    assert final_path.exists()
    assert final_path.read_text(encoding="utf-8") == "done"

    steps = persistence.load_steps(gid)
    assert len(steps) >= 10


def test_failure_recovery_and_clean_termination(monkeypatch, isolated_db):
    def bad_plan(goal, memory=None):
        return {
            "plan_summary": "bad plan",
            "steps": [
                _step(0, "file_write", {"path": "../escape.txt", "content": "oops"}),
                _step(1, "done", {"summary": "done"}),
            ],
        }

    # Keep replans deterministic and bounded.
    monkeypatch.setattr("core.orchestrator.create_plan", bad_plan)
    monkeypatch.setattr("core.orchestrator.replan", lambda *args, **kwargs: {"steps": [_step(0, "file_write", {"path": "../escape.txt", "content": "oops"}), _step(1, "done", {"summary": "done"})]})

    result = Orchestrator().run("Try writing outside workspace", goal_id="recover-stop")
    assert result.startswith("failed:")
    assert (
        "Plan exhausted" in result
        or "workspace" in result.lower()
        or "path" in result.lower()
        or "same failure repeated" in result.lower()
    )


def test_loop_detection_blocks_repeated_failures():
    current = {"tool": "file_write", "tool_input": {"path": "../x.txt", "content": "x"}}
    history = [
        {"tool": "file_write", "tool_input": {"path": "../x.txt", "content": "x"}},
        {"tool": "file_write", "tool_input": {"path": "../x.txt", "content": "x"}},
        {"tool": "file_write", "tool_input": {"path": "../x.txt", "content": "x"}},
    ]
    assert check_loop_detection(history, current) is True


def test_quality_gate_rejects_empty_placeholder_invalid_python(workspace_ctx):
    goal_id, _ = workspace_ctx

    empty_path = "empty.py"
    resolve_workspace_path(empty_path, goal_id=goal_id).write_text("", encoding="utf-8")
    empty_steps = [{
        "tool": "file_write",
        "tool_input": {"path": empty_path},
        "result": {"ok": True, "data": {"path": empty_path}, "error": None, "metadata": {"bytes_written": 0}},
        "status": "success",
    }]
    q_empty = review_quality(f"Write {empty_path}", completed_steps=empty_steps, attempted_steps=empty_steps)
    assert q_empty["passed"] is False

    placeholder_path = "placeholder.py"
    resolve_workspace_path(placeholder_path, goal_id=goal_id).write_text(
        "def main():\n    pass\n\nif __name__ == '__main__':\n    main()\n",
        encoding="utf-8",
    )
    placeholder_steps = [{
        "tool": "file_write",
        "tool_input": {"path": placeholder_path},
        "result": {"ok": True, "data": {"path": placeholder_path}, "error": None, "metadata": {"bytes_written": 64}},
        "status": "success",
    }]
    q_placeholder = review_quality(
        f"Write {placeholder_path}",
        completed_steps=placeholder_steps,
        attempted_steps=placeholder_steps,
    )
    assert q_placeholder["passed"] is False

    invalid_path = "invalid.py"
    resolve_workspace_path(invalid_path, goal_id=goal_id).write_text("def broken(:\n", encoding="utf-8")
    invalid_steps = [{
        "tool": "file_write",
        "tool_input": {"path": invalid_path},
        "result": {"ok": True, "data": {"path": invalid_path}, "error": None, "metadata": {"bytes_written": 13}},
        "status": "success",
    }]
    q_invalid = review_quality(f"Write {invalid_path}", completed_steps=invalid_steps, attempted_steps=invalid_steps)
    assert q_invalid["passed"] is False


def test_workspace_isolation_enforced_for_file_ops(workspace_ctx):
    goal_id, _ = workspace_ctx
    root = workspace_root()

    absolute = tool_registry.file_write(path="/tmp/leak.txt", content="leak")
    traversal = tool_registry.file_write(path="../leak.txt", content="leak")
    safe = tool_registry.file_write(path="safe/out.txt", content="safe")

    assert absolute["ok"] is False
    assert traversal["ok"] is False
    assert safe["ok"] is True

    resolved = Path(safe["data"]["path"]).resolve()
    assert str(resolved).startswith(str(root.resolve()))
    assert str(resolved).startswith(str(goal_workspace_dir(goal_id)))

    assert not Path("/tmp/leak.txt").exists()


def test_observation_layer_has_no_pass_fail_field():
    step = {
        "tool": "file_write",
        "tool_input": {"path": "x.txt", "content": "x"},
        "result": {"ok": False, "data": None, "error": "boom", "metadata": {}},
        "status": "failed",
    }
    observed = evaluate_step(step)
    assert "passed" not in observed  # top-level pass/fail belongs to quality.py only
    assert "observation" in observed
    assert "evaluation" in observed
    assert isinstance(observed["observation"]["issues"], list)
