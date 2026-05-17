from core.verifier import verify_goal


def test_adversarial_smoke_test_compiled_true_executed_false_still_requires_file_exists(tmp_path):
    # Verifier should check filesystem existence + python validity.
    p = tmp_path / "artifact.py"
    p.write_text("print('ok')\n", encoding="utf-8")

    steps = [
        {
            "status": "success",
            "evaluation": {"passed": True, "reason": "ok"},
            "tool": "file_write",
            "tool_input": {"path": str(p)},
            "result": {
                "ok": True,
                "data": None,
                "error": None,
                "metadata": {
                    "bytes_written": p.stat().st_size,
                    # compiled True but executed False -> verifier should still pass but with reduced confidence.
                    "smoke_test": {
                        "compiled": True,
                        "executed": False,
                        "execution_skipped": False,
                        "exit_code": 0,
                        "stdout": "",
                        "stderr": "",
                    },
                },
            },
        }
    ]

    goal = f"Create a file called {p}"
    v = verify_goal(goal, steps, attempted_steps=[])
    assert v["passed"], v
    assert v["confidence"] <= 0.75


def test_adversarial_smoke_test_mismatch_compiled_false_but_runtime_claimed(tmp_path):
    # Fabricate inconsistent smoke_test: compiled False but executed True.
    p = tmp_path / "artifact.py"
    p.write_text("print('ok')\n", encoding="utf-8")

    steps = [
        {
            "status": "success",
            "evaluation": {"passed": True, "reason": "ok"},
            "tool": "file_write",
            "tool_input": {"path": str(p)},
            "result": {
                "ok": True,
                "data": None,
                "error": None,
                "metadata": {
                    "bytes_written": p.stat().st_size,
                    "smoke_test": {
                        "compiled": False,
                        "executed": True,
                        "execution_skipped": False,
                        "exit_code": 0,
                        "stdout": "ok",
                        "stderr": "",
                    },
                },
            },
        }
    ]

    goal = f"Create a file called {p}"
    # Verifier currently gates on compiled; should fail.
    v = verify_goal(goal, steps, attempted_steps=[])
    assert not v["passed"], v

