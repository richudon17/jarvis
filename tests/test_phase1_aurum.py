"""
tests/test_phase1_aurum.py

Phase 1 validation suite for AURUM.
Tests the 6 core reliability guarantees:
  1. Replan limit is hard — exhausts at MAX_REPLAN_ATTEMPTS, never beyond
  2. Placeholder leakage — raw {output} never reaches a tool
  3. Quality bypass — empty completed steps always fails quality gate
  4. Loop detection — same tool+input repeated ≥3 times triggers abort
  5. File round-trip — write then read returns identical content
  6. Trace completeness — execution_trace.json always written, even on abort

Run with:
    pytest tests/test_phase1_aurum.py -v
"""

import json
import sys
from pathlib import Path
from unittest.mock import MagicMock, patch

import pytest

ROOT = Path(__file__).resolve().parents[1]
if str(ROOT) not in sys.path:
    sys.path.insert(0, str(ROOT))


# ─────────────────────────────────────────────────────────────
# Helpers
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


def _make_file_write_step(path: str, step_index: int = 0) -> dict:
    step = _make_step("file_write", {"path": path, "content": "x"}, step_index)
    step["result"]["metadata"]["bytes_written"] = 10
    return step


def _make_run_python_step(stdout: str = "42\n", step_index: int = 0) -> dict:
    step = _make_step("run_python", {"code": "print(42)"}, step_index)
    step["result"]["data"] = {"stdout": stdout, "stderr": ""}
    step["result"]["metadata"]["exit_code"] = 0
    return step


# ─────────────────────────────────────────────────────────────
# TEST 1 — Replan limit
# ─────────────────────────────────────────────────────────────

class TestReplanLimit:
    """Orchestrator must never exceed MAX_REPLAN_ATTEMPTS replans."""

    def test_replan_count_never_exceeds_max(self, tmp_path):
        """Quality always fails → replan triggers → exhausts at limit → final fail."""
        from core.orchestrator import Orchestrator, MAX_REPLAN_ATTEMPTS

        replan_calls = []

        def fake_create_plan(goal, memory=None):
            return {
                "plan_summary": "test",
                "steps": [
                    {"step_index": 0, "description": "done", "tool": "done",
                     "tool_input": {"summary": "done"}},
                ],
            }

        def fake_replan(goal, completed, observed, reason, memory=None):
            replan_calls.append(reason)
            return {
                "plan_summary": "replan",
                "steps": [
                    {"step_index": 0, "description": "done", "tool": "done",
                     "tool_input": {"summary": "done"}},
                ],
            }

        def fake_quality(goal, completed, attempted=None):
            return {
                "passed": False,
                "score": 0.0,
                "issues": ["forced failure"],
                "category": "unknown",
                "reasoning_trace": [],
                "failure_factors": [],
            }

        with (
            patch("core.orchestrator.create_plan", fake_create_plan),
            patch("core.orchestrator.replan", fake_replan),
            patch("core.orchestrator.review_quality", fake_quality),
            patch("core.orchestrator.init_db"),
            patch("core.orchestrator.save_goal"),
            patch("core.orchestrator.update_goal_status"),
            patch("core.orchestrator.save_step"),
            patch("core.orchestrator.reset_orphaned_goals"),
            patch("core.environment.environment_summary", return_value={}),
            patch("core.orchestrator.goal_workspace_dir", return_value=tmp_path),
            patch("core.orchestrator.list_workspace_files", return_value=[]),
            patch("core.orchestrator.MemoryManager"),
        ):
            orc = Orchestrator.__new__(Orchestrator)
            orc.memory = MagicMock()
            result = orc.run("impossible goal that always fails quality", goal_id="test01")

        assert result.startswith("failed:"), f"Expected failure, got: {result}"
        assert len(replan_calls) <= MAX_REPLAN_ATTEMPTS, (
            f"Replan called {len(replan_calls)} times, limit is {MAX_REPLAN_ATTEMPTS}"
        )

    def test_replan_count_resets_per_goal(self, tmp_path):
        """Each orchestrator.run() call starts fresh — replan counter at 0."""
        from core.orchestrator import Orchestrator, MAX_REPLAN_ATTEMPTS
        # The state dict is local to run(), so a new call always starts at replan_count=0.
        # We verify this by ensuring replan_calls in two sequential runs each stay within limit.
        calls_run1 = []
        calls_run2 = []

        def fake_create_plan(goal, memory=None):
            return {"plan_summary": "p", "steps": [
                {"step_index": 0, "description": "d", "tool": "done",
                 "tool_input": {"summary": "d"}}
            ]}

        def make_replan(tracker):
            def fake_replan(goal, completed, observed, reason, memory=None):
                tracker.append(1)
                return {"plan_summary": "rp", "steps": [
                    {"step_index": 0, "description": "d", "tool": "done",
                     "tool_input": {"summary": "d"}}
                ]}
            return fake_replan

        def fake_quality(goal, completed, attempted=None):
            return {"passed": False, "score": 0.0, "issues": ["fail"],
                    "category": "unknown", "reasoning_trace": [], "failure_factors": []}

        patches = dict(
            create_plan=fake_create_plan,
            review_quality=fake_quality,
            init_db=lambda: None,
            save_goal=lambda *a, **k: None,
            update_goal_status=lambda *a, **k: None,
            save_step=lambda *a, **k: None,
            reset_orphaned_goals=lambda: None,
            # environment_summary patched via core.environment
            goal_workspace_dir=lambda *a: tmp_path,
            list_workspace_files=lambda *a: [],
        )

        with (
            patch("core.orchestrator.create_plan", fake_create_plan),
            patch("core.orchestrator.replan", make_replan(calls_run1)),
            patch("core.orchestrator.review_quality", fake_quality),
            patch("core.orchestrator.init_db"),
            patch("core.orchestrator.save_goal"),
            patch("core.orchestrator.update_goal_status"),
            patch("core.orchestrator.save_step"),
            patch("core.orchestrator.reset_orphaned_goals"),
            patch("core.environment.environment_summary", return_value={}),
            patch("core.orchestrator.goal_workspace_dir", return_value=tmp_path),
            patch("core.orchestrator.list_workspace_files", return_value=[]),
            patch("core.orchestrator.MemoryManager"),
        ):
            orc = Orchestrator.__new__(Orchestrator)
            orc.memory = MagicMock()
            orc.run("goal A", goal_id="r1")

        with (
            patch("core.orchestrator.create_plan", fake_create_plan),
            patch("core.orchestrator.replan", make_replan(calls_run2)),
            patch("core.orchestrator.review_quality", fake_quality),
            patch("core.orchestrator.init_db"),
            patch("core.orchestrator.save_goal"),
            patch("core.orchestrator.update_goal_status"),
            patch("core.orchestrator.save_step"),
            patch("core.orchestrator.reset_orphaned_goals"),
            patch("core.environment.environment_summary", return_value={}),
            patch("core.orchestrator.goal_workspace_dir", return_value=tmp_path),
            patch("core.orchestrator.list_workspace_files", return_value=[]),
            patch("core.orchestrator.MemoryManager"),
        ):
            orc2 = Orchestrator.__new__(Orchestrator)
            orc2.memory = MagicMock()
            orc2.run("goal B", goal_id="r2")

        assert len(calls_run1) <= MAX_REPLAN_ATTEMPTS
        assert len(calls_run2) <= MAX_REPLAN_ATTEMPTS


# ─────────────────────────────────────────────────────────────
# TEST 2 — Placeholder leakage
# ─────────────────────────────────────────────────────────────

class TestPlaceholderLeakage:
    """_replace_placeholders must never pass raw {placeholder} strings to tools."""

    def test_known_placeholder_replaced(self):
        from core.orchestrator import _replace_placeholders

        result = _replace_placeholders("{search_results}", previous_context="real data")
        assert result == "real data", f"Expected 'real data', got {result!r}"

    def test_known_placeholder_results(self):
        from core.orchestrator import _replace_placeholders

        result = _replace_placeholders("{results}", previous_context="actual results")
        assert result == "actual results"

    def test_known_placeholder_output(self):
        from core.orchestrator import _replace_placeholders

        result = _replace_placeholders("{output}", previous_context="step output")
        assert result == "step output"

    def test_simple_variable_not_replaced(self):
        """Simple single-word tokens like {x} or {score} should NOT be replaced."""
        from core.orchestrator import _replace_placeholders

        result = _replace_placeholders("{score}", previous_context="real data")
        assert result == "{score}", f"Simple variable should not be replaced, got {result!r}"

    def test_nested_dict_placeholder_replaced(self):
        from core.orchestrator import _replace_placeholders

        tool_input = {"query": "{search_results}", "path": "output.txt"}
        result = _replace_placeholders(tool_input, previous_context="real data")
        assert result["query"] == "real data"
        assert result["path"] == "output.txt"  # path keys are preserved as-is

    def test_nested_list_placeholder_replaced(self):
        from core.orchestrator import _replace_placeholders

        result = _replace_placeholders(["{output}", "literal"], previous_context="ctx")
        assert result[0] == "ctx"
        assert result[1] == "literal"

    def test_empty_context_returns_value_unchanged(self):
        from core.orchestrator import _replace_placeholders

        result = _replace_placeholders("{output}", previous_context="")
        assert result == "{output}"

    def test_non_placeholder_string_untouched(self):
        from core.orchestrator import _replace_placeholders

        result = _replace_placeholders("hello world", previous_context="should not apply")
        assert result == "hello world"

    def test_prefer_latest_uses_latest_context(self):
        from core.orchestrator import _replace_placeholders

        result = _replace_placeholders(
            "{output}",
            previous_context="old",
            latest_context="new",
            prefer_latest=True,
        )
        assert result == "new"


# ─────────────────────────────────────────────────────────────
# TEST 3 — Quality bypass
# ─────────────────────────────────────────────────────────────

class TestQualityBypass:
    """review_quality must reject goals with no meaningful completed steps."""

    def test_empty_completed_fails_unknown(self):
        from core.quality import review_quality

        result = review_quality("do something vague", [], [])
        assert result["passed"] is False, "Empty steps should never pass quality"

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
        """A 'done' step alone should not satisfy a code goal."""
        from core.quality import review_quality

        done_step = _make_step("done", {"summary": "done"}, status="success")
        result = review_quality("write a python script called foo.py", [done_step], [done_step])
        assert result["passed"] is False

    def test_quality_result_has_required_keys(self):
        from core.quality import review_quality

        result = review_quality("some goal", [], [])
        for key in ("passed", "score", "issues", "category", "reasoning_trace", "failure_factors"):
            assert key in result, f"Missing key: {key}"

    def test_score_clamped_between_0_and_1(self):
        from core.quality import review_quality

        result = review_quality("some goal", [], [])
        assert 0.0 <= result["score"] <= 1.0


# ─────────────────────────────────────────────────────────────
# TEST 4 — Loop detection
# ─────────────────────────────────────────────────────────────

class TestLoopDetection:
    """check_loop_detection must trigger after ≥3 identical tool+input combos."""

    def test_triggers_at_threshold(self):
        from core.evaluator import check_loop_detection

        step = {"tool": "web_search", "tool_input": {"query": "same query"}}
        history = [step, step, step]  # 3 identical entries
        assert check_loop_detection(history, step) is True

    def test_does_not_trigger_below_threshold(self):
        from core.evaluator import check_loop_detection

        step = {"tool": "web_search", "tool_input": {"query": "same query"}}
        history = [step, step]  # only 2
        assert check_loop_detection(history, step) is False

    def test_different_inputs_not_detected_as_loop(self):
        from core.evaluator import check_loop_detection

        base = {"tool": "web_search", "tool_input": {"query": "query A"}}
        different = {"tool": "web_search", "tool_input": {"query": "query B"}}
        history = [base, base, base]
        assert check_loop_detection(history, different) is False

    def test_different_tools_not_detected_as_loop(self):
        from core.evaluator import check_loop_detection

        step_a = {"tool": "file_read", "tool_input": {"path": "x.txt"}}
        step_b = {"tool": "file_write", "tool_input": {"path": "x.txt"}}
        history = [step_a, step_a, step_a]
        assert check_loop_detection(history, step_b) is False

    def test_custom_threshold_respected(self):
        from core.evaluator import check_loop_detection

        step = {"tool": "run_python", "tool_input": {"code": "print(1)"}}
        history = [step, step]
        assert check_loop_detection(history, step, threshold=2) is True
        assert check_loop_detection(history, step, threshold=3) is False

    def test_empty_history_never_loops(self):
        from core.evaluator import check_loop_detection

        step = {"tool": "done", "tool_input": {"summary": "done"}}
        assert check_loop_detection([], step) is False


# ─────────────────────────────────────────────────────────────
# TEST 5 — File round-trip
# ─────────────────────────────────────────────────────────────

class TestFileRoundTrip:
    """file_write followed by file_read must return identical content."""

    def test_basic_roundtrip(self, tmp_path):
        from tools.tool_registry import file_write, file_read

        target = str(tmp_path / "roundtrip.txt")
        content = "Hello, AURUM. This is a round-trip test."

        write_result = file_write(target, content)
        assert write_result["ok"] is True, f"Write failed: {write_result['error']}"
        assert write_result["metadata"]["bytes_written"] > 0

        read_result = file_read(target)
        assert read_result["ok"] is True, f"Read failed: {read_result['error']}"
        assert read_result["data"]["content"] == content

    def test_multiline_content_preserved(self, tmp_path):
        from tools.tool_registry import file_write, file_read

        target = str(tmp_path / "multiline.txt")
        content = "line 1\nline 2\nline 3\n"

        file_write(target, content)
        result = file_read(target)
        assert result["data"]["content"] == content

    def test_python_code_roundtrip(self, tmp_path):
        from tools.tool_registry import file_write, file_read

        target = str(tmp_path / "script.py")
        content = "def fib(n):\n    if n <= 1: return n\n    return fib(n-1) + fib(n-2)\n\nprint(fib(10))\n"

        file_write(target, content)
        result = file_read(target)
        assert result["data"]["content"] == content

    def test_unicode_content_preserved(self, tmp_path):
        from tools.tool_registry import file_write, file_read

        target = str(tmp_path / "unicode.txt")
        content = "Hello 世界 — AURUM 🤖"

        file_write(target, content)
        result = file_read(target)
        assert result["data"]["content"] == content

    def test_empty_write_rejected_cleanly(self, tmp_path):
        from tools.tool_registry import file_write

        target = str(tmp_path / "empty.txt")
        result = file_write(target, "")
        # file_write rejects empty content — ok=False, structured error
        assert result["ok"] is False
        assert result["error"] is not None
        assert isinstance(result["error"], str)
        assert len(result["error"]) > 0

    def test_read_nonexistent_file_fails_cleanly(self, tmp_path):
        from tools.tool_registry import file_read

        result = file_read(str(tmp_path / "does_not_exist.txt"))
        assert result["ok"] is False
        assert result["error"] is not None


# ─────────────────────────────────────────────────────────────
# TEST 6 — Trace completeness
# ─────────────────────────────────────────────────────────────

class TestTraceCompleteness:
    """execution_trace.json must always be written, even on crash/abort."""

    def _run_with_crash(self, tmp_path, crash_at: str):
        """Helper: run orchestrator that crashes at a given point, return trace path."""
        from core.orchestrator import Orchestrator

        def fake_create_plan(goal, memory=None):
            if crash_at == "plan":
                raise RuntimeError("Simulated crash during planning")
            return {"plan_summary": "p", "steps": [
                {"step_index": 0, "description": "d", "tool": "done",
                 "tool_input": {"summary": "done"}}
            ]}

        def fake_quality(goal, completed, attempted=None):
            if crash_at == "quality":
                raise RuntimeError("Simulated crash during quality check")
            return {"passed": True, "score": 1.0, "issues": [],
                    "category": "unknown", "reasoning_trace": [], "failure_factors": []}

        try:
            with (
                patch("core.orchestrator.create_plan", fake_create_plan),
                patch("core.orchestrator.review_quality", fake_quality),
                patch("core.orchestrator.init_db"),
                patch("core.orchestrator.save_goal"),
                patch("core.orchestrator.update_goal_status"),
                patch("core.orchestrator.save_step"),
                patch("core.orchestrator.reset_orphaned_goals"),
                patch("core.environment.environment_summary", return_value={}),
                patch("core.orchestrator.goal_workspace_dir", return_value=tmp_path),
                patch("core.orchestrator.list_workspace_files", return_value=[]),
                patch("core.orchestrator.MemoryManager"),
            ):
                orc = Orchestrator.__new__(Orchestrator)
                orc.memory = MagicMock()
                orc.run("test goal", goal_id="trace01")
        except Exception:
            pass  # crash is expected

        return tmp_path / "execution_trace.json"

    def test_trace_written_on_normal_completion(self, tmp_path):
        from core.orchestrator import Orchestrator

        def fake_create_plan(goal, memory=None):
            return {"plan_summary": "p", "steps": [
                {"step_index": 0, "description": "d", "tool": "done",
                 "tool_input": {"summary": "done"}}
            ]}

        def fake_quality(goal, completed, attempted=None):
            return {"passed": True, "score": 1.0, "issues": [],
                    "category": "unknown", "reasoning_trace": [], "failure_factors": []}

        with (
            patch("core.orchestrator.create_plan", fake_create_plan),
            patch("core.orchestrator.review_quality", fake_quality),
            patch("core.orchestrator.init_db"),
            patch("core.orchestrator.save_goal"),
            patch("core.orchestrator.update_goal_status"),
            patch("core.orchestrator.save_step"),
            patch("core.orchestrator.reset_orphaned_goals"),
            patch("core.environment.environment_summary", return_value={}),
            patch("core.orchestrator.goal_workspace_dir", return_value=tmp_path),
            patch("core.orchestrator.list_workspace_files", return_value=[]),
            patch("core.orchestrator.MemoryManager"),
        ):
            orc = Orchestrator.__new__(Orchestrator)
            orc.memory = MagicMock()
            orc.run("test goal", goal_id="trace_ok")

        trace_path = tmp_path / "execution_trace.json"
        assert trace_path.exists(), "execution_trace.json not written on normal completion"

        trace = json.loads(trace_path.read_text())
        assert trace["final_status"] in ("completed", "failed", "aborted")
        assert "timeline" in trace
        assert isinstance(trace["timeline"], list)

    def test_trace_written_on_planning_crash(self, tmp_path):
        trace_path = self._run_with_crash(tmp_path, crash_at="plan")
        assert trace_path.exists(), "execution_trace.json not written after planning crash"

        trace = json.loads(trace_path.read_text())
        assert trace["final_status"] in ("failed", "aborted")

    def test_trace_has_goal_metadata(self, tmp_path):
        from core.orchestrator import Orchestrator

        def fake_create_plan(goal, memory=None):
            return {"plan_summary": "p", "steps": [
                {"step_index": 0, "description": "d", "tool": "done",
                 "tool_input": {"summary": "done"}}
            ]}

        def fake_quality(goal, completed, attempted=None):
            return {"passed": False, "score": 0.0, "issues": ["fail"],
                    "category": "unknown", "reasoning_trace": [], "failure_factors": []}

        with (
            patch("core.orchestrator.create_plan", fake_create_plan),
            patch("core.orchestrator.review_quality", fake_quality),
            patch("core.orchestrator.replan", lambda *a, **k: {"steps": []}),
            patch("core.orchestrator.init_db"),
            patch("core.orchestrator.save_goal"),
            patch("core.orchestrator.update_goal_status"),
            patch("core.orchestrator.save_step"),
            patch("core.orchestrator.reset_orphaned_goals"),
            patch("core.environment.environment_summary", return_value={}),
            patch("core.orchestrator.goal_workspace_dir", return_value=tmp_path),
            patch("core.orchestrator.list_workspace_files", return_value=[]),
            patch("core.orchestrator.MemoryManager"),
        ):
            orc = Orchestrator.__new__(Orchestrator)
            orc.memory = MagicMock()
            orc.run("my specific goal text", goal_id="meta01")

        trace = json.loads((tmp_path / "execution_trace.json").read_text())
        assert trace["goal"] == "my specific goal text"
        assert trace["goal_id"] == "meta01"
        assert "trace_started_at" in trace
        assert "trace_ended_at" in trace

    def test_trace_timeline_has_goal_started_event(self, tmp_path):
        from core.orchestrator import Orchestrator

        def fake_create_plan(goal, memory=None):
            return {"plan_summary": "p", "steps": []}

        def fake_quality(goal, completed, attempted=None):
            return {"passed": False, "score": 0.0, "issues": ["x"],
                    "category": "unknown", "reasoning_trace": [], "failure_factors": []}

        with (
            patch("core.orchestrator.create_plan", fake_create_plan),
            patch("core.orchestrator.review_quality", fake_quality),
            patch("core.orchestrator.init_db"),
            patch("core.orchestrator.save_goal"),
            patch("core.orchestrator.update_goal_status"),
            patch("core.orchestrator.save_step"),
            patch("core.orchestrator.reset_orphaned_goals"),
            patch("core.environment.environment_summary", return_value={}),
            patch("core.orchestrator.goal_workspace_dir", return_value=tmp_path),
            patch("core.orchestrator.list_workspace_files", return_value=[]),
            patch("core.orchestrator.MemoryManager"),
        ):
            orc = Orchestrator.__new__(Orchestrator)
            orc.memory = MagicMock()
            orc.run("goal", goal_id="evt01")

        trace = json.loads((tmp_path / "execution_trace.json").read_text())
        event_types = [e["event_type"] for e in trace["timeline"]]
        assert "goal_started" in event_types


# ─────────────────────────────────────────────────────────────
# TEST 7 — Executor structural contracts
# ─────────────────────────────────────────────────────────────

class TestExecutorContracts:
    """run_step always returns structured dicts; never infers success from strings."""

    def test_done_step_always_succeeds(self):
        from core.executor import run_step

        step = {"step_index": 0, "description": "d", "tool": "done",
                "tool_input": {"summary": "all done"}}
        result = run_step(step)
        assert result["status"] == "done"
        assert result["result"]["ok"] is True
        assert result["result"]["data"]["summary"] == "all done"

    def test_non_dict_tool_result_is_rejected(self):
        from core.executor import run_step

        with patch("core.executor.execute_tool", return_value="raw string result"):
            step = {"step_index": 0, "description": "d", "tool": "file_read",
                    "tool_input": {"path": "x.txt"}}
            result = run_step(step)

        assert result["status"] == "failed"
        assert result["result"]["ok"] is False
        assert "non-structured" in result["result"]["error"]

    def test_successful_tool_result_passes_through(self):
        from core.executor import run_step

        fake_result = {"ok": True, "data": {"content": "hello"}, "error": None, "metadata": {}}
        with patch("core.executor.execute_tool", return_value=fake_result):
            step = {"step_index": 0, "description": "d", "tool": "file_read",
                    "tool_input": {"path": "x.txt"}}
            result = run_step(step)

        assert result["status"] == "success"
        assert result["result"]["ok"] is True

    def test_failed_tool_result_sets_failed_status(self):
        from core.executor import run_step

        fake_result = {"ok": False, "data": None, "error": "file not found", "metadata": {}}
        with patch("core.executor.execute_tool", return_value=fake_result):
            step = {"step_index": 0, "description": "d", "tool": "file_read",
                    "tool_input": {"path": "missing.txt"}}
            result = run_step(step)

        assert result["status"] == "failed"
        assert result["result"]["error"] == "file not found"


# ─────────────────────────────────────────────────────────────
# TEST 8 — Memory manager
# ─────────────────────────────────────────────────────────────

class TestShortTermMemory:
    """ShortTermMemory must be an isolated in-memory store."""

    def test_set_and_get(self):
        from memory.memory_manager import ShortTermMemory

        mem = ShortTermMemory()
        mem.set("goal", "write a script")
        assert mem.get("goal") == "write a script"

    def test_get_missing_key_returns_default(self):
        from memory.memory_manager import ShortTermMemory

        mem = ShortTermMemory()
        assert mem.get("nonexistent") is None
        assert mem.get("nonexistent", "fallback") == "fallback"

    def test_clear_empties_store(self):
        from memory.memory_manager import ShortTermMemory

        mem = ShortTermMemory()
        mem.set("key", "value")
        mem.clear()
        assert mem.get("key") is None

    def test_snapshot_is_copy(self):
        from memory.memory_manager import ShortTermMemory

        mem = ShortTermMemory()
        mem.set("x", 1)
        snap = mem.snapshot()
        snap["x"] = 999
        assert mem.get("x") == 1  # original unaffected

    def test_two_instances_are_isolated(self):
        from memory.memory_manager import ShortTermMemory

        m1 = ShortTermMemory()
        m2 = ShortTermMemory()
        m1.set("key", "from_m1")
        assert m2.get("key") is None