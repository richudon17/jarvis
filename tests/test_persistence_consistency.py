import os
from pathlib import Path

import state.persistence as persistence
from core.artifact import make_artifact_record


def test_persistence_serialize_deserialize_roundtrip(tmp_path, monkeypatch):
    tmp_db = tmp_path / "state.db"
    monkeypatch.setattr(persistence, "DB_PATH", str(tmp_db))
    persistence.init_db()

    goal_id = "persist-1"
    persistence.save_goal(goal_id, "Save artifact test", status="running")

    artifact = make_artifact_record(
        artifact_type="python",
        path=str(tmp_path / "a.py"),
        created=True,
        verified=True,
        quality_score=0.9,
        semantic_confidence=0.95,
        runtime_validated=True,
        issues=["none"],
        warnings=[],
        evidence=["py_compile successful"],
    )

    # Save a step containing artifact metadata in result.metadata
    persistence.save_step(
        goal_id=goal_id,
        step_index=1,
        description="write artifact",
        tool="file_write",
        tool_input={"path": artifact["path"]},
        result={"ok": True, "data": None, "error": None, "metadata": {"artifact": artifact}},
        status="success",
    )

    steps = persistence.load_steps(goal_id)
    assert len(steps) == 1
    loaded_meta = (steps[0].get("result") or {}).get("metadata") or {}
    loaded_art = loaded_meta.get("artifact")
    assert isinstance(loaded_art, dict)
    # Basic field equality checks
    assert loaded_art.get("artifact_type") == "python"
    assert float(loaded_art.get("quality_score")) == 0.9
    assert loaded_art.get("runtime_validated") is True
