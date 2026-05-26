from core.verifier import verify_goal
from core.artifact import make_artifact_record


def test_adversarial_fabricated_smoke_test_missing_file():
    # Fabricated smoke_test claims compiled/executed but file does not exist on disk.
    fake_path = "/tmp/nonexistent_fake_artifact.py"

    fake_smoke = {
        "compiled": True,
        "executed": True,
        "exit_code": 0,
        "stdout": "hello",
        "stderr": "",
    }

    completed_steps = [
        {
            "status": "success",
            "evaluation": {"passed": True, "reason": "ok"},
            "tool": "file_write",
            "tool_input": {"path": fake_path},
            "result": {"ok": True, "data": None, "error": None, "metadata": {"bytes_written": 10, "smoke_test": fake_smoke}},
        }
    ]

    goal = f"Create a file called {fake_path}"
    v = verify_goal(goal, completed_steps, attempted_steps=[])
    # Verifier must check actual file existence and should fail.
    assert not v["passed"], f"Verifier accepted fabricated evidence: {v}"
