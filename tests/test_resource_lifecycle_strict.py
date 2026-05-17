import gc
import os
from pathlib import Path

import pytest

import state.persistence as persistence
from core.browser import PLAYWRIGHT_AVAILABLE


def test_persistence_repeated_init_and_ops_no_unclosed_conns(tmp_path, monkeypatch):
    # Route persistence to a temp db.
    tmp_db = tmp_path / "state.db"
    monkeypatch.setattr(persistence, "DB_PATH", str(tmp_db))

    # Stress: repeated init/save/load cycles.
    for i in range(50):
        persistence.init_db()
        gid = f"gid-{i}"
        persistence.save_goal(gid, f"goal-{i}", status="running")
        persistence.update_goal_status(gid, status="completed")
        persistence.save_step(
            goal_id=gid,
            step_index=0,
            description="d",
            tool="file_write",
            tool_input={"path": f"/tmp/x{i}.py"},
            result={"ok": True},
            status="success",
        )
        persistence.load_goal(gid)
        persistence.load_steps(gid)

    # Force GC to surface any unraisable ResourceWarnings.
    gc.collect()


def test_persistence_db_exception_path_cleanup(tmp_path, monkeypatch):
    tmp_db = tmp_path / "state.db"
    monkeypatch.setattr(persistence, "DB_PATH", str(tmp_db))
    persistence.init_db()

    gid = "gid-exc"
    persistence.save_goal(gid, "goal", status="running")

    # Trigger an exception mid-transaction by using an invalid SQL type
    # (SQLite will still need to close the connection when the context exits).
    with pytest.raises(Exception):
        # Create invalid goal_id type for primary key binding.
        persistence.save_goal(object(), "bad", status="running")

    gc.collect()


@pytest.mark.skipif(not PLAYWRIGHT_AVAILABLE, reason="playwright not installed")
def test_browser_partial_start_failure_cleanup(monkeypatch):
    # Force sync_playwright().start() to raise.
    import core.browser as b

    class DummyPlaywright:
        def stop(self):
            return None

    monkeypatch.setattr(b, "sync_playwright", lambda: type("X", (), {"start": lambda self: (_ for _ in ()).throw(RuntimeError("boom"))})())

    # Calling start should not leak sockets/loops; cleanup is asserted by pytest -W error.
    try:
        b._browser.start(headless=True)
    except Exception:
        pass
    finally:
        # Ensure best-effort cleanup.
        b._browser.stop()

    gc.collect()


def test_import_safety_reimport_persistence(tmp_path, monkeypatch):
    # Ensure importing persistence doesn't allocate leaked resources.
    tmp_db = tmp_path / "state.db"
    monkeypatch.setattr(persistence, "DB_PATH", str(tmp_db))

    # Import/reload behavior can trigger warnings if module does IO at import.
    import importlib
    importlib.reload(persistence)
    gc.collect()

