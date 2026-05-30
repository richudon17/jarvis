"""
tests/test_phase1_phase2_aurum.py

Combined Phase 1 + Phase 2 validation suite for AURUM.

Phase 1 — Core engine reliability:
  1. Replan limit is hard
  2. Placeholder leakage prevention
  3. Quality bypass prevention
  4. Loop detection
  5. File round-trip correctness
  6. Trace completeness
  7. Executor structural contracts
  8. Short-term memory isolation

Phase 2 — State persistence & resume:
  9.  list_interrupted_goals / abandon_goal
  10. Short-term memory write-through & restore
  11. Orchestrator resume — state rebuilt from DB steps
  12. Orchestrator resume — uses replan not create_plan
  13. Quality category: code+calculation goals classified as code
  14. Quality calculation: run_file accepted as execution evidence
  15. Quality calculation: output file accepted when no stdout

Run with:
    pytest tests/test_phase1_phase2_aurum.py -v
"""

from __future__ import annotations

import json
import sqlite3
import sys
import tempfile
from pathlib import Path
from unittest.mock import MagicMock, patch, call

import pytest

ROOT = Path(__file__).resolve().parents[1]
if str(ROOT) not in sys.path:
    sys.path.insert(0, str(ROOT))


# ─────────────────────────────────────────────────────────────
# Shared helpers
# ─────────────────────────────────────────────────────────────

def _make_step(tool: str, tool_input: dict, step_index: int = 0, status: str = "success") -> dict:
    return {
        "step_index": step_index,
        "description": f"test step {step_index}",
        "tool": tool,
        "tool_input": tool_input,
        "result": {
            "ok": status == "success",
            "data": {"content": "some content"} if tool == "file_read" else {"stdout": "42"},
            "error": None if status == "success" else "deliberate failure",
            "metadata": {},
        },
        "status": status,
        "observation": {
            "tool": tool,
            "status": status,
            "ok": status == "success",
            "error": None,
            "issues": [] if status == "success" else ["deliberate failure"],
            "detected_errors": [],
            "anomalies": [],
            "output_summary": "",
            "has_data": True,
        },
        "evaluation": {
            "passed": status == "success",
            "issues": [],
        },
    }


def _orch_patches(tmp_path, extra: dict | None = None):
    """Return standard orchestrator patch context managers."""
    patches = [
        patch("core.orchestrator.create_plan", lambda goal, memory=None: {
            "plan_summary": "p",
            "steps": [{"step_index": 0, "description": "d", "tool": "done",
                        "tool_input": {"summary": "done"}}],
        }),
        patch("core.orchestrator.replan", lambda *a, **k: {
            "plan_summary": "rp",
            "steps": [{"step_index": 0, "description": "d", "tool": "done",
                        "tool_input": {"summary": "done"}}],
        }),
        patch("core.orchestrator.review_quality", lambda goal, completed, attempted=None: {
            "passed": True, "score": 1.0, "issues": [],
            "category": "unknown", "reasoning_trace": [], "failure_factors": [],
        }),
        patch("core.orchestrator.init_db"),
        patch("core.orchestrator.save_goal"),
        patch("core.orchestrator.update_goal_status"),
        patch("core.orchestrator.save_step"),
        patch("core.orchestrator.reset_orphaned_goals"),
        patch("core.environment.environment_summary", return_value=""),
        patch("core.orchestrator.goal_workspace_dir", return_value=tmp_path),
        patch("core.orchestrator.list_workspace_files", return_value=[]),
        patch("core.orchestrator.MemoryManager"),
    ]
    if extra:
        for target, value in extra.items():
            patches.append(patch(target, value))
    return patches


# ═════════════════════════════════════════════════════════════
# PHASE 1 TESTS
# ═════════════════════════════════════════════════════════════

class TestReplanLimit:
    """P1-1: Orchestrator must never exceed MAX_REPLAN_ATTEMPTS replans."""

    def test_replan_count_never_exceeds_max(self, tmp_path):
        from core.orchestrator import Orchestrator, MAX_REPLAN_ATTEMPTS

        replan_calls = []

        def fake_replan(goal, completed, observed, reason, memory=None):
            replan_calls.append(reason)
            return {"plan_summary": "rp", "steps": [
                {"step_index": 0, "description": "d", "tool": "done",
                 "tool_input": {"summary": "done"}}
            ]}

        def fake_quality(goal, completed, attempted=None):
            return {"passed": False, "score": 0.0, "issues": ["forced failure"],
                    "category": "unknown", "reasoning_trace": [], "failure_factors": []}

        with (
            patch("core.orchestrator.create_plan", lambda g, memory=None: {
                "plan_summary": "p", "steps": [
                    {"step_index": 0, "description": "d", "tool": "done",
                     "tool_input": {"summary": "done"}}
                ]}),
            patch("core.orchestrator.replan", fake_replan),
            patch("core.orchestrator.review_quality", fake_quality),
            patch("core.orchestrator.init_db"),
            patch("core.orchestrator.save_goal"),
            patch("core.orchestrator.update_goal_status"),
            patch("core.orchestrator.save_step"),
            patch("core.orchestrator.reset_orphaned_goals"),
            patch("core.environment.environment_summary", return_value=""),
            patch("core.orchestrator.goal_workspace_dir", return_value=tmp_path),
            patch("core.orchestrator.list_workspace_files", return_value=[]),
            patch("core.orchestrator.MemoryManager"),
        ):
            orc = Orchestrator.__new__(Orchestrator)
            orc.memory = MagicMock()
            result = orc.run("impossible goal", goal_id="t01")

        assert result.startswith("failed:")
        assert len(replan_calls) <= MAX_REPLAN_ATTEMPTS

    def test_replan_resets_per_run(self, tmp_path):
        from core.orchestrator import Orchestrator, MAX_REPLAN_ATTEMPTS

        calls_r1, calls_r2 = [], []

        def make_replan(tracker):
            def r(goal, completed, observed, reason, memory=None):
                tracker.append(1)
                return {"plan_summary": "rp", "steps": [
                    {"step_index": 0, "description": "d", "tool": "done",
                     "tool_input": {"summary": "done"}}
                ]}
            return r

        def fake_quality(goal, completed, attempted=None):
            return {"passed": False, "score": 0.0, "issues": ["fail"],
                    "category": "unknown", "reasoning_trace": [], "failure_factors": []}

        common = dict(
            create_plan=lambda g, memory=None: {"plan_summary": "p", "steps": [
                {"step_index": 0, "description": "d", "tool": "done",
                 "tool_input": {"summary": "done"}}]},
            review_quality=fake_quality,
            init_db=lambda: None, save_goal=lambda *a, **k: None,
            update_goal_status=lambda *a, **k: None, save_step=lambda *a, **k: None,
            reset_orphaned_goals=lambda: None,
        )

        for tracker in (calls_r1, calls_r2):
            with (
                patch("core.orchestrator.create_plan", common["create_plan"]),
                patch("core.orchestrator.replan", make_replan(tracker)),
                patch("core.orchestrator.review_quality", fake_quality),
                patch("core.orchestrator.init_db"),
                patch("core.orchestrator.save_goal"),
                patch("core.orchestrator.update_goal_status"),
                patch("core.orchestrator.save_step"),
                patch("core.orchestrator.reset_orphaned_goals"),
                patch("core.environment.environment_summary", return_value=""),
                patch("core.orchestrator.goal_workspace_dir", return_value=tmp_path),
                patch("core.orchestrator.list_workspace_files", return_value=[]),
                patch("core.orchestrator.MemoryManager"),
            ):
                orc = Orchestrator.__new__(Orchestrator)
                orc.memory = MagicMock()
                orc.run("goal", goal_id=f"r{len(tracker)}")

        assert len(calls_r1) <= MAX_REPLAN_ATTEMPTS
        assert len(calls_r2) <= MAX_REPLAN_ATTEMPTS


class TestPlaceholderLeakage:
    """P1-2: _replace_placeholders must never pass raw placeholders to tools."""

    def test_known_placeholder_replaced(self):
        from core.orchestrator import _replace_placeholders
        assert _replace_placeholders("{search_results}", previous_context="real data") == "real data"

    def test_known_placeholder_results(self):
        from core.orchestrator import _replace_placeholders
        assert _replace_placeholders("{results}", previous_context="actual") == "actual"

    def test_known_placeholder_output(self):
        from core.orchestrator import _replace_placeholders
        assert _replace_placeholders("{output}", previous_context="step output") == "step output"

    def test_simple_variable_not_replaced(self):
        from core.orchestrator import _replace_placeholders
        assert _replace_placeholders("{score}", previous_context="real data") == "{score}"

    def test_nested_dict_placeholder_replaced(self):
        from core.orchestrator import _replace_placeholders
        result = _replace_placeholders({"query": "{search_results}", "path": "out.txt"}, previous_context="real data")
        assert result["query"] == "real data"
        assert result["path"] == "out.txt"

    def test_nested_list_placeholder_replaced(self):
        from core.orchestrator import _replace_placeholders
        result = _replace_placeholders(["{output}", "literal"], previous_context="ctx")
        assert result[0] == "ctx"
        assert result[1] == "literal"

    def test_empty_context_unchanged(self):
        from core.orchestrator import _replace_placeholders
        assert _replace_placeholders("{output}", previous_context="") == "{output}"

    def test_non_placeholder_untouched(self):
        from core.orchestrator import _replace_placeholders
        assert _replace_placeholders("hello world", previous_context="x") == "hello world"

    def test_prefer_latest(self):
        from core.orchestrator import _replace_placeholders
        assert _replace_placeholders("{output}", previous_context="old", latest_context="new", prefer_latest=True) == "new"


class TestQualityBypass:
    """P1-3: review_quality must reject goals with no meaningful completed steps."""

    def test_empty_completed_fails_unknown(self):
        from core.quality import review_quality
        assert review_quality("do something", [], [])["passed"] is False

    def test_empty_completed_code_goal_fails(self):
        from core.quality import review_quality
        result = review_quality("write a python script hello.py", [], [])
        assert result["passed"] is False
        assert result["category"] == "code"

    def test_empty_completed_research_goal_fails(self):
        from core.quality import review_quality
        result = review_quality("search for the latest AI news", [], [])
        assert result["passed"] is False
        assert result["category"] == "research"

    def test_empty_completed_calculation_goal_fails(self):
        from core.quality import review_quality
        result = review_quality("calculate the first 50 prime numbers", [], [])
        assert result["passed"] is False
        assert result["category"] == "calculation"

    def test_done_step_only_not_enough_for_code(self):
        from core.quality import review_quality
        done_step = _make_step("done", {"summary": "done"})
        assert review_quality("write a python script foo.py", [done_step], [done_step])["passed"] is False

    def test_quality_result_has_required_keys(self):
        from core.quality import review_quality
        result = review_quality("some goal", [], [])
        for key in ("passed", "score", "issues", "category", "reasoning_trace", "failure_factors"):
            assert key in result

    def test_score_clamped(self):
        from core.quality import review_quality
        result = review_quality("some goal", [], [])
        assert 0.0 <= result["score"] <= 1.0


class TestLoopDetection:
    """P1-4: check_loop_detection must trigger after repeated identical steps."""

    def test_triggers_at_threshold(self):
        from core.evaluator import check_loop_detection
        step = {"tool": "web_search", "tool_input": {"query": "same"}}
        assert check_loop_detection([step, step, step], step) is True

    def test_does_not_trigger_below_threshold(self):
        from core.evaluator import check_loop_detection
        step = {"tool": "web_search", "tool_input": {"query": "same"}}
        assert check_loop_detection([step, step], step) is False

    def test_different_inputs_not_loop(self):
        from core.evaluator import check_loop_detection
        base = {"tool": "web_search", "tool_input": {"query": "A"}}
        diff = {"tool": "web_search", "tool_input": {"query": "B"}}
        assert check_loop_detection([base, base, base], diff) is False

    def test_different_tools_not_loop(self):
        from core.evaluator import check_loop_detection
        a = {"tool": "file_read", "tool_input": {"path": "x.txt"}}
        b = {"tool": "file_write", "tool_input": {"path": "x.txt"}}
        assert check_loop_detection([a, a, a], b) is False

    def test_custom_threshold(self):
        from core.evaluator import check_loop_detection
        step = {"tool": "run_python", "tool_input": {"code": "print(1)"}}
        assert check_loop_detection([step, step], step, threshold=2) is True
        assert check_loop_detection([step, step], step, threshold=3) is False

    def test_empty_history_never_loops(self):
        from core.evaluator import check_loop_detection
        step = {"tool": "done", "tool_input": {"summary": "done"}}
        assert check_loop_detection([], step) is False

    def test_dict_key_order_independent(self):
        """Loop detection must not be fooled by different dict key ordering."""
        from core.evaluator import check_loop_detection
        s1 = {"tool": "web_search", "tool_input": {"query": "q", "max_results": 5}}
        s2 = {"tool": "web_search", "tool_input": {"max_results": 5, "query": "q"}}
        # s1 and s2 are semantically identical — should detect as loop
        assert check_loop_detection([s1, s1, s1], s2) is True


class TestFileRoundTrip:
    """P1-5: file_write then file_read must return identical content."""

    def test_basic_roundtrip(self, tmp_path):
        from tools.tool_registry import file_write, file_read
        target = str(tmp_path / "rt.txt")
        content = "Hello AURUM round-trip."
        assert file_write(target, content)["ok"] is True
        assert file_read(target)["data"]["content"] == content

    def test_multiline_preserved(self, tmp_path):
        from tools.tool_registry import file_write, file_read
        target = str(tmp_path / "ml.txt")
        content = "line 1\nline 2\nline 3\n"
        file_write(target, content)
        assert file_read(target)["data"]["content"] == content

    def test_python_code_roundtrip(self, tmp_path):
        from tools.tool_registry import file_write, file_read
        target = str(tmp_path / "s.py")
        content = "def fib(n):\n    return n if n <= 1 else fib(n-1)+fib(n-2)\nprint(fib(10))\n"
        file_write(target, content)
        assert file_read(target)["data"]["content"] == content

    def test_unicode_preserved(self, tmp_path):
        from tools.tool_registry import file_write, file_read
        target = str(tmp_path / "u.txt")
        content = "Hello 世界 — AURUM 🤖"
        file_write(target, content)
        assert file_read(target)["data"]["content"] == content

    def test_empty_write_rejected(self, tmp_path):
        from tools.tool_registry import file_write
        result = file_write(str(tmp_path / "empty.txt"), "")
        assert result["ok"] is False
        assert result["error"]

    def test_read_missing_file_fails_cleanly(self, tmp_path):
        from tools.tool_registry import file_read
        result = file_read(str(tmp_path / "nope.txt"))
        assert result["ok"] is False
        assert result["error"]


class TestTraceCompleteness:
    """P1-6: execution_trace.json always written, even on crash."""

    def _run_with_patches(self, tmp_path, extra_patches=None, goal_id="tr01"):
        from core.orchestrator import Orchestrator
        patches = [
            patch("core.orchestrator.create_plan", lambda g, memory=None: {
                "plan_summary": "p", "steps": [
                    {"step_index": 0, "description": "d", "tool": "done",
                     "tool_input": {"summary": "done"}}]}),
            patch("core.orchestrator.review_quality", lambda g, c, attempted=None: {
                "passed": True, "score": 1.0, "issues": [],
                "category": "unknown", "reasoning_trace": [], "failure_factors": []}),
            patch("core.orchestrator.init_db"),
            patch("core.orchestrator.save_goal"),
            patch("core.orchestrator.update_goal_status"),
            patch("core.orchestrator.save_step"),
            patch("core.orchestrator.reset_orphaned_goals"),
            patch("core.environment.environment_summary", return_value=""),
            patch("core.orchestrator.goal_workspace_dir", return_value=tmp_path),
            patch("core.orchestrator.list_workspace_files", return_value=[]),
            patch("core.orchestrator.MemoryManager"),
        ]
        if extra_patches:
            patches.extend(extra_patches)
        try:
            with patches[0], patches[1], patches[2], patches[3], patches[4], patches[5], patches[6], patches[7], patches[8], patches[9], patches[10]:
                orc = Orchestrator.__new__(Orchestrator)
                orc.memory = MagicMock()
                orc.run("test goal", goal_id=goal_id)
        except Exception:
            pass
        return tmp_path / "execution_trace.json"

    def test_trace_written_on_completion(self, tmp_path):
        trace = self._run_with_patches(tmp_path)
        assert trace.exists()
        data = json.loads(trace.read_text())
        assert data["final_status"] in ("completed", "failed", "aborted")

    def test_trace_has_goal_metadata(self, tmp_path):
        trace = self._run_with_patches(tmp_path, goal_id="meta01")
        data = json.loads(trace.read_text())
        assert data["goal"] == "test goal"
        assert data["goal_id"] == "meta01"
        assert "trace_started_at" in data
        assert "trace_ended_at" in data

    def test_trace_has_goal_started_event(self, tmp_path):
        trace = self._run_with_patches(tmp_path)
        data = json.loads(trace.read_text())
        event_types = [e["event_type"] for e in data["timeline"]]
        assert "goal_started" in event_types

    def test_trace_written_on_planning_crash(self, tmp_path):
        from core.orchestrator import Orchestrator

        def crashing_plan(goal, memory=None):
            raise RuntimeError("planned crash")

        try:
            with (
                patch("core.orchestrator.create_plan", crashing_plan),
                patch("core.orchestrator.review_quality", lambda *a, **k: {}),
                patch("core.orchestrator.init_db"),
                patch("core.orchestrator.save_goal"),
                patch("core.orchestrator.update_goal_status"),
                patch("core.orchestrator.save_step"),
                patch("core.orchestrator.reset_orphaned_goals"),
                patch("core.environment.environment_summary", return_value=""),
                patch("core.orchestrator.goal_workspace_dir", return_value=tmp_path),
                patch("core.orchestrator.list_workspace_files", return_value=[]),
                patch("core.orchestrator.MemoryManager"),
            ):
                orc = Orchestrator.__new__(Orchestrator)
                orc.memory = MagicMock()
                orc.run("crash goal", goal_id="crash01")
        except Exception:
            pass

        trace = tmp_path / "execution_trace.json"
        assert trace.exists()
        data = json.loads(trace.read_text())
        assert data["final_status"] in ("failed", "aborted")


class TestExecutorContracts:
    """P1-7: run_step always returns structured dicts."""

    def test_done_step_always_succeeds(self):
        from core.executor import run_step
        step = {"step_index": 0, "description": "d", "tool": "done", "tool_input": {"summary": "all done"}}
        result = run_step(step)
        assert result["status"] == "done"
        assert result["result"]["ok"] is True
        assert result["result"]["data"]["summary"] == "all done"

    def test_non_dict_tool_result_rejected(self):
        from core.executor import run_step
        with patch("core.executor.execute_tool", return_value="raw string"):
            step = {"step_index": 0, "description": "d", "tool": "file_read", "tool_input": {"path": "x.txt"}}
            result = run_step(step)
        assert result["status"] == "failed"
        assert result["result"]["ok"] is False

    def test_successful_tool_result_passes_through(self):
        from core.executor import run_step
        fake = {"ok": True, "data": {"content": "hello"}, "error": None, "metadata": {}}
        with patch("core.executor.execute_tool", return_value=fake):
            step = {"step_index": 0, "description": "d", "tool": "file_read", "tool_input": {"path": "x.txt"}}
            result = run_step(step)
        assert result["status"] == "success"

    def test_failed_tool_result_sets_failed_status(self):
        from core.executor import run_step
        fake = {"ok": False, "data": None, "error": "not found", "metadata": {}}
        with patch("core.executor.execute_tool", return_value=fake):
            step = {"step_index": 0, "description": "d", "tool": "file_read", "tool_input": {"path": "x.txt"}}
            result = run_step(step)
        assert result["status"] == "failed"
        assert result["result"]["error"] == "not found"


class TestShortTermMemory:
    """P1-8: ShortTermMemory in-memory isolation."""

    def test_set_and_get(self):
        from memory.memory_manager import ShortTermMemory
        m = ShortTermMemory()
        m.set("goal", "write a script")
        assert m.get("goal") == "write a script"

    def test_get_missing_returns_default(self):
        from memory.memory_manager import ShortTermMemory
        m = ShortTermMemory()
        assert m.get("nonexistent") is None
        assert m.get("nonexistent", "fallback") == "fallback"

    def test_clear_empties_store(self):
        from memory.memory_manager import ShortTermMemory
        m = ShortTermMemory()
        m.set("key", "value")
        m.clear()
        assert m.get("key") is None

    def test_snapshot_is_copy(self):
        from memory.memory_manager import ShortTermMemory
        m = ShortTermMemory()
        m.set("x", 1)
        snap = m.snapshot()
        snap["x"] = 999
        assert m.get("x") == 1

    def test_two_instances_isolated(self):
        from memory.memory_manager import ShortTermMemory
        m1, m2 = ShortTermMemory(), ShortTermMemory()
        m1.set("key", "from_m1")
        assert m2.get("key") is None


# ═════════════════════════════════════════════════════════════
# PHASE 2 TESTS
# ═════════════════════════════════════════════════════════════

@pytest.fixture()
def isolated_db(tmp_path, monkeypatch):
    """Give each test its own SQLite DB so they don't interfere."""
    db_path = str(tmp_path / "test_aurum.db")
    monkeypatch.setattr("state.persistence.DB_PATH", db_path)
    monkeypatch.setattr("memory.memory_manager.DB_PATH", db_path)
    from state.persistence import init_db
    init_db()
    return db_path


class TestPersistenceResume:
    """P2-9: list_interrupted_goals / abandon_goal / load_steps."""

    def test_list_interrupted_goals_empty(self, isolated_db):
        from state.persistence import list_interrupted_goals
        assert list_interrupted_goals() == []

    def test_list_interrupted_goals_finds_running(self, isolated_db):
        from state.persistence import save_goal, list_interrupted_goals
        save_goal("g1", "do something", status="running")
        result = list_interrupted_goals()
        assert len(result) == 1
        assert result[0]["id"] == "g1"

    def test_completed_goals_not_listed_as_interrupted(self, isolated_db):
        from state.persistence import save_goal, update_goal_status, list_interrupted_goals
        save_goal("g2", "done goal", status="running")
        update_goal_status("g2", "completed")
        assert list_interrupted_goals() == []

    def test_abandon_goal_removes_from_interrupted(self, isolated_db):
        from state.persistence import save_goal, abandon_goal, list_interrupted_goals
        save_goal("g3", "interrupted goal", status="running")
        assert len(list_interrupted_goals()) == 1
        abandon_goal("g3")
        assert list_interrupted_goals() == []

    def test_abandon_goal_sets_abandoned_status(self, isolated_db):
        from state.persistence import save_goal, abandon_goal, load_goal
        save_goal("g4", "to abandon", status="running")
        abandon_goal("g4")
        assert load_goal("g4")["status"] == "abandoned"

    def test_load_steps_returns_saved_steps(self, isolated_db):
        from state.persistence import save_goal, save_step, load_steps
        save_goal("g5", "multi-step goal", status="running")
        save_step("g5", 0, "write file", "file_write", {"path": "x.txt", "content": "hi"}, {"ok": True}, "success")
        save_step("g5", 1, "read file", "file_read", {"path": "x.txt"}, {"ok": True}, "success")
        steps = load_steps("g5")
        assert len(steps) == 2
        assert steps[0]["tool"] == "file_write"
        assert steps[1]["tool"] == "file_read"

    def test_load_steps_deserializes_tool_input(self, isolated_db):
        from state.persistence import save_goal, save_step, load_steps
        save_goal("g6", "goal", status="running")
        save_step("g6", 0, "write", "file_write", {"path": "a.txt", "content": "hello"}, {"ok": True}, "success")
        steps = load_steps("g6")
        assert isinstance(steps[0]["tool_input"], dict)
        assert steps[0]["tool_input"]["path"] == "a.txt"

    def test_multiple_interrupted_ordered_by_recency(self, isolated_db):
        from state.persistence import save_goal, list_interrupted_goals
        import time
        save_goal("older", "older goal", status="running")
        time.sleep(0.01)
        save_goal("newer", "newer goal", status="running")
        results = list_interrupted_goals()
        assert results[0]["id"] == "newer"


class TestShortTermMemoryPersistence:
    """P2-10: ShortTermMemory write-through and restore across restarts."""

    def test_persistent_keys_written_to_db(self, isolated_db):
        from memory.memory_manager import ShortTermMemory
        m = ShortTermMemory()
        m.bind_goal("goal123")
        m.set("goal", "write a script")

        conn = sqlite3.connect(isolated_db)
        rows = conn.execute(
            "SELECT key, value FROM memory WHERE memory_type='short_term'"
        ).fetchall()
        conn.close()
        keys = [r[0] for r in rows]
        assert "goal123:goal" in keys

    def test_non_persistent_keys_not_written(self, isolated_db):
        from memory.memory_manager import ShortTermMemory
        m = ShortTermMemory()
        m.bind_goal("goal123")
        m.set("environment", "some env data")

        conn = sqlite3.connect(isolated_db)
        rows = conn.execute(
            "SELECT key FROM memory WHERE memory_type='short_term' AND key='goal123:environment'"
        ).fetchall()
        conn.close()
        assert len(rows) == 0

    def test_restore_on_bind_goal(self, isolated_db):
        from memory.memory_manager import ShortTermMemory

        # Write in first instance
        m1 = ShortTermMemory()
        m1.bind_goal("goal456")
        m1.set("goal", "original goal text")
        m1.set("goal_id", "goal456")

        # Restore in second instance (simulates restart)
        m2 = ShortTermMemory()
        m2.bind_goal("goal456")
        assert m2.get("goal") == "original goal text"
        assert m2.get("goal_id") == "goal456"

    def test_different_goals_dont_bleed(self, isolated_db):
        from memory.memory_manager import ShortTermMemory

        m1 = ShortTermMemory()
        m1.bind_goal("goalA")
        m1.set("goal", "goal A text")

        m2 = ShortTermMemory()
        m2.bind_goal("goalB")
        assert m2.get("goal") is None

    def test_upsert_updates_existing_key(self, isolated_db):
        from memory.memory_manager import ShortTermMemory

        m = ShortTermMemory()
        m.bind_goal("goalC")
        m.set("goal", "first value")
        m.set("goal", "updated value")

        conn = sqlite3.connect(isolated_db)
        rows = conn.execute(
            "SELECT value FROM memory WHERE memory_type='short_term' AND key='goalC:goal'"
        ).fetchall()
        conn.close()
        # Should be exactly one row with the updated value
        assert len(rows) == 1
        assert rows[0][0] == "updated value"

    def test_persistence_failure_does_not_crash(self, isolated_db):
        """A broken DB path must never crash the agent."""
        from memory.memory_manager import ShortTermMemory
        import memory.memory_manager as mm
        original = mm.DB_PATH
        mm.DB_PATH = "/nonexistent/path/db.sqlite"
        try:
            m = ShortTermMemory()
            m.bind_goal("safe")
            m.set("goal", "should not crash")  # must not raise
            assert m.get("goal") == "should not crash"  # in-memory still works
        finally:
            mm.DB_PATH = original

    def test_no_goal_id_no_persistence(self, isolated_db):
        """Without bind_goal, no DB writes should happen."""
        from memory.memory_manager import ShortTermMemory
        m = ShortTermMemory()
        m.set("goal", "ephemeral")

        conn = sqlite3.connect(isolated_db)
        rows = conn.execute("SELECT * FROM memory WHERE memory_type='short_term'").fetchall()
        conn.close()
        assert len(rows) == 0


class TestOrchestratorResume:
    """P2-11/12: Orchestrator rebuilds state from DB steps on resume."""

    def test_resume_restores_completed_steps(self, tmp_path):
        from core.orchestrator import Orchestrator

        prior_steps = [
            {"step_index": 0, "description": "write file", "tool": "file_write",
             "tool_input": {"path": "x.txt", "content": "hi"},
             "result": {"ok": True, "data": {}, "error": None, "metadata": {}},
             "status": "success"},
        ]

        replan_calls = []

        def fake_replan(goal, completed, observed, reason, memory=None):
            replan_calls.append({"completed_count": len(completed)})
            return {"plan_summary": "rp", "steps": [
                {"step_index": 1, "description": "done", "tool": "done",
                 "tool_input": {"summary": "done"}}
            ]}

        with (
            patch("core.orchestrator.create_plan", lambda g, memory=None: {
                "plan_summary": "p", "steps": []}),
            patch("core.orchestrator.replan", fake_replan),
            patch("core.orchestrator.review_quality", lambda g, c, attempted=None: {
                "passed": True, "score": 1.0, "issues": [],
                "category": "unknown", "reasoning_trace": [], "failure_factors": []}),
            patch("core.orchestrator.init_db"),
            patch("core.orchestrator.save_goal"),
            patch("core.orchestrator.update_goal_status"),
            patch("core.orchestrator.save_step"),
            patch("core.orchestrator.reset_orphaned_goals"),
            patch("core.environment.environment_summary", return_value=""),
            patch("core.orchestrator.goal_workspace_dir", return_value=tmp_path),
            patch("core.orchestrator.list_workspace_files", return_value=[]),
            patch("core.orchestrator.MemoryManager"),
        ):
            orc = Orchestrator.__new__(Orchestrator)
            orc.memory = MagicMock()
            result = orc.run("my goal", goal_id="resume01", resume_from_steps=prior_steps)

        # replan should have been called with 1 completed step restored
        assert len(replan_calls) == 1
        assert replan_calls[0]["completed_count"] == 1

    def test_resume_uses_replan_not_create_plan(self, tmp_path):
        """On resume with completed steps, replan must be called, not create_plan."""
        from core.orchestrator import Orchestrator

        create_plan_calls = []
        replan_calls = []

        def fake_create_plan(goal, memory=None):
            create_plan_calls.append(1)
            return {"plan_summary": "p", "steps": [
                {"step_index": 0, "description": "d", "tool": "done",
                 "tool_input": {"summary": "done"}}
            ]}

        def fake_replan(goal, completed, observed, reason, memory=None):
            replan_calls.append(1)
            return {"plan_summary": "rp", "steps": [
                {"step_index": 1, "description": "done", "tool": "done",
                 "tool_input": {"summary": "done"}}
            ]}

        prior_steps = [
            {"step_index": 0, "description": "write", "tool": "file_write",
             "tool_input": {"path": "f.py", "content": "x"},
             "result": {"ok": True, "data": {}, "error": None, "metadata": {}},
             "status": "success"},
        ]

        with (
            patch("core.orchestrator.create_plan", fake_create_plan),
            patch("core.orchestrator.replan", fake_replan),
            patch("core.orchestrator.review_quality", lambda g, c, attempted=None: {
                "passed": True, "score": 1.0, "issues": [],
                "category": "unknown", "reasoning_trace": [], "failure_factors": []}),
            patch("core.orchestrator.init_db"),
            patch("core.orchestrator.save_goal"),
            patch("core.orchestrator.update_goal_status"),
            patch("core.orchestrator.save_step"),
            patch("core.orchestrator.reset_orphaned_goals"),
            patch("core.environment.environment_summary", return_value=""),
            patch("core.orchestrator.goal_workspace_dir", return_value=tmp_path),
            patch("core.orchestrator.list_workspace_files", return_value=[]),
            patch("core.orchestrator.MemoryManager"),
        ):
            orc = Orchestrator.__new__(Orchestrator)
            orc.memory = MagicMock()
            orc.run("my goal", goal_id="resume02", resume_from_steps=prior_steps)

        assert create_plan_calls == [], "create_plan should NOT be called on resume"
        assert len(replan_calls) == 1, "replan MUST be called on resume"

    def test_fresh_run_uses_create_plan(self, tmp_path):
        """Without resume_from_steps, create_plan must be called."""
        from core.orchestrator import Orchestrator

        create_plan_calls = []
        replan_calls = []

        def fake_create_plan(goal, memory=None):
            create_plan_calls.append(1)
            return {"plan_summary": "p", "steps": [
                {"step_index": 0, "description": "d", "tool": "done",
                 "tool_input": {"summary": "done"}}
            ]}

        def fake_replan(goal, completed, observed, reason, memory=None):
            replan_calls.append(1)
            return {"plan_summary": "rp", "steps": []}

        with (
            patch("core.orchestrator.create_plan", fake_create_plan),
            patch("core.orchestrator.replan", fake_replan),
            patch("core.orchestrator.review_quality", lambda g, c, attempted=None: {
                "passed": True, "score": 1.0, "issues": [],
                "category": "unknown", "reasoning_trace": [], "failure_factors": []}),
            patch("core.orchestrator.init_db"),
            patch("core.orchestrator.save_goal"),
            patch("core.orchestrator.update_goal_status"),
            patch("core.orchestrator.save_step"),
            patch("core.orchestrator.reset_orphaned_goals"),
            patch("core.environment.environment_summary", return_value=""),
            patch("core.orchestrator.goal_workspace_dir", return_value=tmp_path),
            patch("core.orchestrator.list_workspace_files", return_value=[]),
            patch("core.orchestrator.MemoryManager"),
        ):
            orc = Orchestrator.__new__(Orchestrator)
            orc.memory = MagicMock()
            orc.run("fresh goal", goal_id="fresh01")

        assert len(create_plan_calls) == 1
        assert replan_calls == []

    def test_resume_trace_has_goal_resumed_event(self, tmp_path):
        from core.orchestrator import Orchestrator

        prior_steps = [
            {"step_index": 0, "description": "write", "tool": "file_write",
             "tool_input": {"path": "f.txt", "content": "x"},
             "result": {"ok": True, "data": {}, "error": None, "metadata": {}},
             "status": "success"},
        ]

        with (
            patch("core.orchestrator.create_plan", lambda g, memory=None: {"steps": []}),
            patch("core.orchestrator.replan", lambda *a, **k: {"plan_summary": "rp", "steps": [
                {"step_index": 1, "description": "d", "tool": "done",
                 "tool_input": {"summary": "done"}}
            ]}),
            patch("core.orchestrator.review_quality", lambda g, c, attempted=None: {
                "passed": True, "score": 1.0, "issues": [],
                "category": "unknown", "reasoning_trace": [], "failure_factors": []}),
            patch("core.orchestrator.init_db"),
            patch("core.orchestrator.save_goal"),
            patch("core.orchestrator.update_goal_status"),
            patch("core.orchestrator.save_step"),
            patch("core.orchestrator.reset_orphaned_goals"),
            patch("core.environment.environment_summary", return_value=""),
            patch("core.orchestrator.goal_workspace_dir", return_value=tmp_path),
            patch("core.orchestrator.list_workspace_files", return_value=[]),
            patch("core.orchestrator.MemoryManager"),
        ):
            orc = Orchestrator.__new__(Orchestrator)
            orc.memory = MagicMock()
            orc.run("resumed goal", goal_id="res_trace", resume_from_steps=prior_steps)

        trace = json.loads((tmp_path / "execution_trace.json").read_text())
        event_types = [e["event_type"] for e in trace["timeline"]]
        assert "goal_resumed" in event_types
        assert "goal_started" not in event_types


class TestQualityPhase2:
    """P2-13/14/15: Quality fixes for code+calculation and run_file evidence."""

    def test_code_calc_goal_with_py_file_classified_as_code(self):
        """fibonacci + .py filename → code, not calculation."""
        from core.quality import _infer_category
        filenames = ["fib.py", "fib_output.txt"]
        category = _infer_category(
            "write a python script that calculates fibonacci numbers and save to fib.py",
            filenames, []
        )
        assert category == "code", f"Expected 'code', got '{category}'"

    def test_pure_calculation_no_py_file_classified_as_calculation(self):
        """fibonacci with no .py file → calculation."""
        from core.quality import _infer_category
        category = _infer_category(
            "calculate the fibonacci sequence up to 1000",
            [], []
        )
        assert category == "calculation"

    def test_calculation_accepts_run_file_evidence(self):
        """run_file counts as execution evidence for calculation goals."""
        from core.quality import review_quality

        run_file_step = {
            "step_index": 0, "description": "run it", "tool": "run_file",
            "tool_input": {"path": "script.py"},
            "result": {
                "ok": True,
                "data": {"stdout": "1 1 2 3 5 8\n"},
                "error": None,
                "metadata": {"exit_code": 0},
            },
            "status": "success",
            "observation": {}, "evaluation": {"passed": True, "issues": []},
        }
        result = review_quality("calculate the sum of primes", [run_file_step])
        assert result["passed"] is True
        assert result["category"] == "calculation"

    def test_calculation_accepts_output_file_when_no_stdout(self, tmp_path):
        """No stdout but output file written → calculation passes."""
        from core.quality import review_quality
        from core.workspace import set_execution_context, goal_workspace_dir, clear_execution_context

        goal_id = "calc_out_test"
        set_execution_context(goal_id)
        output_file = goal_workspace_dir(goal_id) / "results.txt"
        output_file.write_text("42\n43\n44\n")

        try:
            run_file_step = {
                "step_index": 0, "tool": "run_file",
                "tool_input": {"path": "script.py"},
                "result": {"ok": True, "data": {"stdout": ""}, "error": None, "metadata": {}},
                "status": "success",
                "description": "run", "observation": {}, "evaluation": {"passed": True, "issues": []},
            }
            write_step = {
                "step_index": 1, "tool": "file_write",
                "tool_input": {"path": "results.txt", "content": "42\n43\n44\n"},
                "result": {"ok": True, "data": {}, "error": None,
                           "metadata": {"bytes_written": 9}},
                "status": "success",
                "description": "save output", "observation": {}, "evaluation": {"passed": True, "issues": []},
            }
            result = review_quality("calculate primes", [run_file_step, write_step])
            assert result["passed"] is True
        finally:
            clear_execution_context()

    def test_calculation_fails_without_any_run_evidence(self):
        from core.quality import review_quality
        result = review_quality("calculate the sum of numbers", [], [])
        assert result["passed"] is False
        assert result["category"] == "calculation"

    def test_run_python_still_preferred_over_run_file(self):
        """If both run_python and run_file exist, run_python stdout is used."""
        from core.quality import review_quality

        run_python_step = {
            "step_index": 0, "tool": "run_python",
            "tool_input": {"code": "print(42)"},
            "result": {"ok": True, "data": {"stdout": "42\n"}, "error": None, "metadata": {}},
            "status": "success",
            "description": "run py", "observation": {}, "evaluation": {"passed": True, "issues": []},
        }
        run_file_step = {
            "step_index": 1, "tool": "run_file",
            "tool_input": {"path": "s.py"},
            "result": {"ok": True, "data": {"stdout": ""}, "error": None, "metadata": {}},
            "status": "success",
            "description": "run file", "observation": {}, "evaluation": {"passed": True, "issues": []},
        }
        result = review_quality("calculate something", [run_python_step, run_file_step])
        assert result["passed"] is True