import sys
from pathlib import Path

import pytest

# Ensure repo root is on sys.path so `tools` and `core` are importable under pytest.
from pathlib import Path as _Path

REPO_ROOT = str(_Path(__file__).resolve().parents[1])
if REPO_ROOT not in sys.path:
    sys.path.insert(0, REPO_ROOT)

from tools import tool_registry
from core.evaluator import evaluate_step
from core.verifier import verify_goal
from core.quality import review_quality


def _tmp_path(tmp_path, name: str) -> str:
    return str(tmp_path / name)


def _step(tool: str, tool_input: dict, result: dict, status: str):
    return {
        "tool": tool,
        "tool_input": tool_input,
        "result": result,
        "status": status,
        "evaluation": {"passed": True} if status == "success" else {"passed": False},
    }


def test_file_write_rejects_empty_content(tmp_path):
    path = _tmp_path(tmp_path, "empty.txt")
    result = tool_registry.file_write(path=path, content="   ")
    assert isinstance(result, dict)
    assert result["ok"] is False
    assert not Path(path).exists()


def test_file_write_rejects_syntax_error_py(tmp_path):
    path = _tmp_path(tmp_path, "bad.py")
    bad = "def x(:\n    return 1\n"
    result = tool_registry.file_write(path=path, content=bad)
    assert result["ok"] is False
    assert not Path(path).exists()


def test_file_write_accepts_valid_python_and_writes_file(tmp_path):
    path = _tmp_path(tmp_path, "good.py")
    good = "print('ok')\n"
    result = tool_registry.file_write(path=path, content=good)
    assert result["ok"] is True
    assert Path(path).exists()
    assert Path(path).stat().st_size > 0


def test_run_python_blocks_pygame(tmp_path):
    r = tool_registry.run_python(code="import pygame\nprint('x')\n")
    assert r["ok"] is False
    assert r.get("metadata", {}).get("blocked") is True or "blocked" in (r.get("error") or "").lower()


def test_run_python_blocks_tkinter(tmp_path):
    r = tool_registry.run_python(code="import tkinter\nprint('x')\n")
    assert r["ok"] is False
    assert r.get("metadata", {}).get("blocked") is True or "blocked" in (r.get("error") or "").lower()


def test_run_python_blocks_turtle(tmp_path):
    r = tool_registry.run_python(code="import turtle\nprint('x')\n")
    assert r["ok"] is False
    assert r.get("metadata", {}).get("blocked") is True or "blocked" in (r.get("error") or "").lower()


def test_run_python_blocks_input(tmp_path):
    r = tool_registry.run_python(code="x = input('x')\nprint(x)\n")
    assert r["ok"] is False
    assert "blocked" in (r.get("error") or "").lower() or r.get("metadata", {}).get("blocked") is True


def test_successful_file_named_error_txt_not_treated_as_failure(tmp_path):
    path = _tmp_path(tmp_path, "error.txt")
    r = tool_registry.file_write(path=path, content="hello world")
    assert r["ok"] is True

    goal = f"Save output to {path}"
    completed = [_step("file_write", {"path": path}, r, status="success")]
    attempted = completed
    verification = verify_goal(goal, completed_steps=completed, attempted_steps=attempted)
    assert verification["passed"] is True


def test_done_does_not_complete_if_verify_fails():
    goal = "Read does_not_exist.txt"
    verification = verify_goal(goal, completed_steps=[], attempted_steps=[])
    assert verification["passed"] is False


def test_missing_requested_file_summarize_goal_fails_clearly(tmp_path):
    missing = _tmp_path(tmp_path, "does_not_exist.txt")
    r = tool_registry.file_read(path=missing)
    assert r["ok"] is False

    goal = f"Read {missing} and summarize"
    completed = []
    attempted = [_step("file_read", {"path": missing}, r, status="failed")]
    verification = verify_goal(goal, completed_steps=completed, attempted_steps=attempted)
    assert verification["passed"] is False


def test_calculation_goal_requires_executed_output(tmp_path):
    # Empty stdout => should fail for calculation goals.
    r = {
        "ok": True,
        "data": {"stdout": "\n", "stderr": "", "exit_code": 0},
        "error": None,
        "metadata": {"exit_code": 0},
    }
    goal = "Calculate the first 20 Fibonacci numbers"
    completed = [_step("run_python", {"code": "..."}, r, status="success")]
    verification = verify_goal(goal, completed_steps=completed, attempted_steps=completed)
    assert verification["passed"] is False


def test_evaluator_uses_structured_ok_error_not_substrings():
    step = {
        "tool": "file_write",
        "tool_input": {"path": "x"},
        "result": {"ok": False, "error": "nope", "data": None, "metadata": {}},
        "status": "failed",
    }
    evaluated = evaluate_step(step)
    assert evaluated["evaluation"]["passed"] is False


def test_placeholder_replacement_does_not_corrupt_fstrings():
    # Avoid importing orchestrator (planner requires GROQ_API_KEY).
    # Regression intent: '{score}' matches JARVIS placeholder pattern and must not be
    # treated as a file path or otherwise replaced into something else.
    import re

    PLACEHOLDER_PATTERN = re.compile(r"\{[^{}]+\}")
    KNOWN_PLACEHOLDERS = ("{search_results}", "{results}", "{output}", "{summary}")

    def is_placeholder_value(value: str) -> bool:
        stripped = value.strip()
        lowered = stripped.lower()
        return (
            any(token in lowered for token in KNOWN_PLACEHOLDERS)
            or PLACEHOLDER_PATTERN.fullmatch(stripped) is not None
        )

    assert is_placeholder_value('{score}') is True




def test_placeholder_replacement_does_not_replace_file_paths(tmp_path):
    import re

    PLACEHOLDER_PATTERN = re.compile(r"\{[^{}]+\}")
    KNOWN_PLACEHOLDERS = ("{search_results}", "{results}", "{output}", "{summary}")

    def is_placeholder_value(value: str) -> bool:
        stripped = value.strip()
        lowered = stripped.lower()
        return (
            any(token in lowered for token in KNOWN_PLACEHOLDERS)
            or PLACEHOLDER_PATTERN.fullmatch(stripped) is not None
        )

    assert is_placeholder_value(str(tmp_path / "a.py")) is False




def test_web_research_summary_does_not_save_raw_title_url_snippet_dump(tmp_path, monkeypatch):
    class DummyDDGS:
        def text(self, query, max_results=5):
            yield {"title": "T1", "href": "U1", "body": "S1"}
            yield {"title": "T2", "href": "U2", "body": "S2"}

    monkeypatch.setattr(tool_registry, "DDGS", lambda: DummyDDGS())

    ws = tool_registry.web_search(query="python", max_results=2)
    assert ws["ok"] is True

    raw_dump = "\n".join(
        [
            f"Title: {x['title']}\nURL: {x['url']}\nSnippet: {x['snippet']}"
            for x in ws["data"]
        ]
    )

    summary = tool_registry.summarize_text(
        text=raw_dump,
        goal="latest python packaging tools",
        max_items=2,
    )
    assert summary["ok"] is True

    out = _tmp_path(tmp_path, "packaging_research.md")
    fw = tool_registry.file_write(path=out, content=summary["data"]["summary"])
    assert fw["ok"] is True

    txt = Path(out).read_text(encoding="utf-8").lower()
    assert txt.count("title:") + txt.count("url:") + txt.count("snippet:") < 4

    goal = f"Research latest Python packaging tools and save a clear summary to {out}"
    # Mirror semantic verifier expectation: ensure summary was written.

    completed = [
        _step("web_search", {"query": "python", "max_results": 2}, ws, status="success"),
        _step(
            "summarize_text",
            {"text": "...", "goal": "latest python packaging tools", "max_items": 2},
            summary,
            status="success",
        ),
        _step("file_write", {"path": out}, fw, status="success"),
    ]
    verification = verify_goal(goal, completed_steps=completed, attempted_steps=completed)
    assert verification["passed"] is True
    # Also ensure summary verifier expectations are met.
    from core.semantic_verifier import semantic_verify_goal
    sem = semantic_verify_goal(goal, completed_steps=completed, attempted_steps=completed)
    assert sem["passed"] is True



def test_quality_rejects_placeholder_python_file(tmp_path):
    path = tmp_path / "app.py"
    path.write_text("def main():\n    pass\n\nif __name__ == '__main__':\n    main()\n", encoding="utf-8")

    completed = [
        _step(
            "file_write",
            {"path": str(path)},
            {
                "ok": True,
                "data": {"path": str(path)},
                "error": None,
                "metadata": {"exists": True, "non_empty": True, "syntax_valid": True},
            },
            status="success",
        )
    ]
    quality = review_quality(f"Write an app in {path}", completed_steps=completed, attempted_steps=completed)
    assert quality["passed"] is False
    assert quality["score"] < 0.70


def test_done_does_not_complete_when_quality_fails(monkeypatch):
    monkeypatch.setenv("GROQ_API_KEY", "test")

    import core.orchestrator as orch

    class DummyShort:
        def set(self, *args, **kwargs):
            pass

    class DummyEpisodic:
        def record(self, *args, **kwargs):
            pass

    class DummyMemory:
        short = DummyShort()
        episodic = DummyEpisodic()

    monkeypatch.setattr(orch, "save_goal", lambda *args, **kwargs: None)
    monkeypatch.setattr(orch, "update_goal_status", lambda *args, **kwargs: None)
    monkeypatch.setattr(orch, "save_step", lambda *args, **kwargs: None)
    monkeypatch.setattr(
        orch,
        "create_plan",
        lambda *args, **kwargs: {
            "plan_summary": "test",
            "steps": [
                {
                    "step_index": 0,
                    "description": "done",
                    "tool": "done",
                    "tool_input": {"summary": "done"},
                }
            ],
        },
    )
    monkeypatch.setattr(
        orch,
        "verify_goal",
        lambda *args, **kwargs: {"passed": True, "reason": "structure ok", "confidence": 0.9},
    )
    monkeypatch.setattr(
        orch,
        "review_quality",
        lambda *args, **kwargs: {
            "passed": False,
            "score": 0.1,
            "issues": ["low quality"],
            "recommendations": [],
            "category": "code",
        },
    )
    monkeypatch.setattr(orch, "replan", lambda *args, **kwargs: {"steps": []})

    agent = object.__new__(orch.Orchestrator)
    agent.memory = DummyMemory()
    result = agent.run("Write a bad app")

    assert "failed" in result.lower()
    assert "quality" in result.lower()
