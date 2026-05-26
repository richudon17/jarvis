"""
test_evaluator_logic.py
Tests the step evaluator for robust error handling and structured output parsing.
"""

import sys
from pathlib import Path

sys.path.insert(0, str(Path(__file__).parent))

from core.evaluator import evaluate_step


def mock_step(tool: str, status: str, result=None, **kwargs) -> dict:
    """Create a mock step."""
    return {
        "step_index": 0,
        "tool": tool,
        "tool_input": kwargs.get("input", {}),
        "status": status,
        "result": result,
    }


# Test Cases
EVAL_TESTS = {
    "Structured file_write success": {
        "step": mock_step(
            "file_write",
            "success",
            {
                "ok": True,
                "data": None,
                "error": None,
                "metadata": {"bytes_written": 100},
            },
        ),
        "expect_passed": True,
    },
    "file_write with zero bytes (FAIL)": {
        "step": mock_step(
            "file_write",
            "success",
            {
                "ok": True,
                "data": None,
                "error": None,
                "metadata": {"bytes_written": 0},
            },
        ),
        "expect_passed": False,
    },
    "Malformed result (string instead of dict)": {
        "step": mock_step("run_python", "success", "some output text"),
        "expect_passed": True,  # Should normalize to structured form
    },
    "Empty result (None)": {
        "step": mock_step("run_python", "success", None),
        "expect_passed": False,
    },
    "Tool failed (ok=False)": {
        "step": mock_step(
            "web_search",
            "success",
            {
                "ok": False,
                "data": None,
                "error": "Search failed",
                "metadata": {},
            },
        ),
        "expect_passed": False,
    },
    "Done step (always passes)": {
        "step": {
            "step_index": 0,
            "tool": "done",
            "status": "done",
            "result": None,
        },
        "expect_passed": True,
    },
}


def run_evaluator_tests():
    """Run all evaluator tests."""
    print("\n" + "=" * 80)
    print("EVALUATOR ROBUSTNESS TESTS")
    print("=" * 80)

    passed = 0
    failed = 0

    for test_name, test_data in EVAL_TESTS.items():
        step = test_data["step"]
        expect_passed = test_data["expect_passed"]

        result = evaluate_step(step)
        evaluation = result.get("evaluation", {})
        actually_passed = evaluation.get("passed", False)

        matches = actually_passed == expect_passed
        status = "✓ PASS" if matches else "❌ FAIL"

        print(f"\n{status}: {test_name}")
        print(f"  Expected: {expect_passed}, Got: {actually_passed}")
        if evaluation.get("reason"):
            print(f"  Reason: {evaluation['reason']}")

        if matches:
            passed += 1
        else:
            failed += 1

    print("\n" + "=" * 80)
    print(f"SUMMARY: {passed} passed, {failed} failed out of {len(EVAL_TESTS)}")
    print("=" * 80)


if __name__ == "__main__":
    run_evaluator_tests()
