import state.persistence as persistence
from core.artifact import make_artifact_record
from core.verifier import verify_goal


def test_spoofed_db_smoke_test_cannot_override_missing_file(tmp_path, monkeypatch):
    tmp_db = tmp_path / "state.db"
    monkeypatch.setattr(persistence, "DB_PATH", str(tmp_db))
    persistence.init_db()

    goal_id = "spoof-1"
    persistence.save_goal(goal_id, "Create a file called fake.py", status="running")

    fake_path = str(tmp_path / "fake.py")
    fake_smoke = {"compiled": True, "executed": True}

    # Directly persist a step claiming the file was written and smoke_test passed, but do NOT create the file on disk.
    persistence.save_step(
        goal_id=goal_id,
        step_index=1,
        description="write fake",
        tool="file_write",
        tool_input={"path": fake_path},
        result={"ok": True, "data": None, "error": None, "metadata": {"bytes_written": 10, "smoke_test": fake_smoke}},
        status="success",
    )

    steps = persistence.load_steps(goal_id)
    # Verify that verifier still fails because file is not present on filesystem.
    goal = f"Create a file called {fake_path}"
    v = verify_goal(goal, steps, attempted_steps=[])
    assert not v["passed"]
