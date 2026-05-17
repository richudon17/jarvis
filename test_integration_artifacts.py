import os
from pathlib import Path
from core.smoke_test import smoke_test_python_file
from core.quality import review_quality
from core.verifier import verify_goal


def test_integration_python_artifact_happy_path(tmp_path):
    p = tmp_path / "good.py"
    p.write_text('print("hello world")\n')
    smoke = smoke_test_python_file(str(p))
    assert smoke.get("compiled")

    completed_steps = [
        {
            "status": "success",
            "evaluation": {"passed": True, "reason": "ok"},
            "tool": "file_write",
            "tool_input": {"path": str(p)},
            "result": {"ok": True, "data": None, "error": None, "metadata": {"bytes_written": p.stat().st_size, "smoke_test": smoke}},
        }
    ]

    goal = f"Create a file called {p}"
    v = verify_goal(goal, completed_steps, attempted_steps=[])
    assert v["passed"], v

    q = review_quality(goal, completed_steps=completed_steps)
    assert q["passed"], q


def test_integration_python_artifact_compile_fail(tmp_path):
    p = tmp_path / "bad.py"
    p.write_text('def oops(:\n    pass\n')
    smoke = smoke_test_python_file(str(p))
    assert not smoke.get("compiled")

    completed_steps = [
        {
            "status": "success",
            "evaluation": {"passed": True, "reason": "ok"},
            "tool": "file_write",
            "tool_input": {"path": str(p)},
            "result": {"ok": True, "data": None, "error": None, "metadata": {"bytes_written": p.stat().st_size, "smoke_test": smoke}},
        }
    ]

    goal = f"Create a file called {p}"
    v = verify_goal(goal, completed_steps, attempted_steps=[])
    assert not v["passed"]