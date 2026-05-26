from __future__ import annotations

import uuid

import pytest

import memory.memory_manager as memory_manager
import state.persistence as persistence
from core.orchestrator import Orchestrator
from core.planner import create_plan, replan
from core.quality import review_quality
from core.workspace import clear_execution_context, resolve_workspace_path, set_execution_context
from interface.goal_input import prompt_goal
from tools import tool_registry


@pytest.fixture
def isolated_db(tmp_path, monkeypatch):
    db = tmp_path / "aurum_terminal_state.db"
    monkeypatch.setattr(persistence, "DB_PATH", str(db))
    monkeypatch.setattr(memory_manager, "DB_PATH", str(db))
    persistence.init_db()
    return db


def _completed_write(path: str, content: str) -> dict:
    result = tool_registry.file_write(path=path, content=content)
    assert result["ok"] is True
    return {
        "step_index": 0,
        "description": f"Write {path}",
        "tool": "file_write",
        "tool_input": {"path": path, "content": content},
        "result": result,
        "status": "success",
    }


def test_terminal_multiline_prompt_preserves_newlines(monkeypatch):
    inputs = iter([
        "create notes.txt with 3 lines:\\",
        "apple",
        "banana",
        "orange",
        "then read it back",
        "END",
    ])
    monkeypatch.setattr("builtins.input", lambda _prompt: next(inputs))

    goal = prompt_goal()

    assert goal == "create notes.txt with 3 lines:\napple\nbanana\norange\nthen read it back"


def test_plain_file_goal_strips_text_prefix_and_avoids_output_py():
    plan = create_plan("create a file called hello.txt with the text hello world")
    steps = plan["steps"]

    assert [step["tool"] for step in steps] == ["file_write", "done"]
    assert steps[0]["tool_input"] == {"path": "hello.txt", "content": "hello world"}
    assert all((step.get("tool_input") or {}).get("path") != "output.py" for step in steps)


def test_multiline_file_goal_writes_lines_then_reads_back():
    plan = create_plan(
        "create notes.txt with 3 lines:\n"
        "apple\n"
        "banana\n"
        "orange\n"
        "then read it back"
    )
    steps = plan["steps"]

    assert [step["tool"] for step in steps] == ["file_write", "file_read", "done"]
    assert steps[0]["tool_input"] == {
        "path": "notes.txt",
        "content": "apple\nbanana\norange",
    }
    assert steps[1]["tool_input"] == {"path": "notes.txt"}
    assert steps[2]["tool_input"] == {"summary": "{output}"}


def test_replace_contents_direct_overwrite_quality_passes():
    goal_id = f"terminal-replace-{uuid.uuid4().hex[:8]}"
    set_execution_context(goal_id, "replace test")
    try:
        completed = [_completed_write("hello.txt", "goodbye world")]
        quality = review_quality(
            "replace the contents of hello.txt with goodbye world",
            completed_steps=completed,
            attempted_steps=completed,
        )
    finally:
        clear_execution_context()

    assert quality["passed"] is True
    assert quality["category"] == "file"


def test_replan_handles_dict_results_and_normalizes_steps(monkeypatch):
    monkeypatch.setattr(
        "core.planner._call_llm",
        lambda *args, **kwargs: '{"steps":[{"description":"List files","tool":"file_list","tool_input":{"directory":"."}}]}',
    )

    plan = replan(
        "read missing_file.txt",
        completed_steps=[{"step_index": 0, "description": "Read", "result": {"ok": True}}],
        failed_step={"tool": "file_read", "tool_input": {"path": "missing_file.txt"}},
        failure_reason="file not found",
    )

    assert [step["tool"] for step in plan["steps"]] == ["file_list", "done"]
    assert plan["steps"][0]["step_index"] == 0
    assert plan["steps"][1]["step_index"] == 1


def test_missing_file_read_fails_without_replan_or_hallucinated_content(monkeypatch, isolated_db):
    missing = f"missing_{uuid.uuid4().hex}.txt"
    called_replan = False

    def fail_if_replanned(*args, **kwargs):
        nonlocal called_replan
        called_replan = True
        raise AssertionError("missing-file read should not replan")

    monkeypatch.setattr("core.orchestrator.replan", fail_if_replanned)

    result = Orchestrator().run(f"read {missing}", goal_id=f"terminal-missing-{uuid.uuid4().hex[:8]}")

    assert result.startswith("failed:")
    assert "File not found" in result
    assert called_replan is False


def test_read_goal_recovers_exact_prior_workspace_artifact(isolated_db):
    filename = f"terminal_prior_{uuid.uuid4().hex}.txt"
    prior_goal = f"prior-{uuid.uuid4().hex[:8]}"
    set_execution_context(prior_goal, "prior artifact")
    try:
        prior = tool_registry.file_write(path=filename, content="hello world")
        assert prior["ok"] is True
    finally:
        clear_execution_context()

    read_goal = f"terminal-read-{uuid.uuid4().hex[:8]}"
    result = Orchestrator().run(f"read the contents of {filename}", goal_id=read_goal)

    assert result == "completed: hello world"
    current_path = resolve_workspace_path(filename, goal_id=read_goal)
    assert current_path.name == filename
    assert current_path.read_text(encoding="utf-8") == "hello world"
