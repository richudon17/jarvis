"""
test_harness_offline.py
Offline testing that doesn't require Groq API key.
Tests semantic verification, evaluator, and verifier logic directly.
"""

import sys
import json
from pathlib import Path
from datetime import datetime

sys.path.insert(0, str(Path(__file__).parent))

from core.semantic_verifier import semantic_verify_goal
from core.evaluator import evaluate_step

# ============================================================================
# MOCK COMPLETED STEPS FOR TESTING
# ============================================================================

def mock_file_write_step(path: str, content: str, size_bytes: int = None) -> dict:
    """Mock a successful file_write step."""
    return {
        "step_index": 0,
        "tool": "file_write",
        "tool_input": {"path": path, "content": content},
        "status": "success",
        "result": {
            "ok": True,
            "data": None,
            "error": None,
            "metadata": {"bytes_written": size_bytes or len(content)},
        },
        "evaluation": {"passed": True},
    }


def mock_empty_file_write_step(path: str) -> dict:
    """Mock a file_write that creates an empty file."""
    return {
        "step_index": 0,
        "tool": "file_write",
        "tool_input": {"path": path, "content": ""},
        "status": "success",
        "result": {
            "ok": True,
            "data": None,
            "error": None,
            "metadata": {"bytes_written": 0},
        },
        "evaluation": {"passed": False, "reason": "file_write produced empty file"},
    }


def mock_placeholder_file_write_step(path: str) -> dict:
    """Mock a file_write with placeholder content."""
    content = "# This is placeholder code\npass"
    return {
        "step_index": 0,
        "tool": "file_write",
        "tool_input": {"path": path, "content": content},
        "status": "success",
        "result": {
            "ok": True,
            "data": None,
            "error": None,
            "metadata": {"bytes_written": len(content)},
        },
        "evaluation": {"passed": True},
    }


def mock_good_calculation_step(stdout: str) -> dict:
    """Mock a run_python step with actual output."""
    return {
        "step_index": 0,
        "tool": "run_python",
        "tool_input": {"code": "print(sum(range(10)))"},
        "status": "success",
        "result": {
            "ok": True,
            "data": {"stdout": stdout, "stderr": ""},
            "error": None,
            "metadata": {},
        },
        "evaluation": {"passed": True},
    }


def mock_empty_calculation_step() -> dict:
    """Mock a run_python step with no output."""
    return {
        "step_index": 0,
        "tool": "run_python",
        "tool_input": {"code": "# does nothing"},
        "status": "success",
        "result": {
            "ok": True,
            "data": {"stdout": "", "stderr": ""},
            "error": None,
            "metadata": {},
        },
        "evaluation": {"passed": True},
    }


def mock_research_summary_step(summary: str) -> dict:
    """Mock a summarize_text step."""
    return {
        "step_index": 0,
        "tool": "summarize_text",
        "tool_input": {"text": "dummy"},
        "status": "success",
        "result": {
            "ok": True,
            "data": {"summary": summary},
            "error": None,
            "metadata": {},
        },
        "evaluation": {"passed": True},
    }


def mock_research_summary_file_step(path: str, content: str) -> dict:
    """Mock a file_write with research summary content."""
    return {
        "step_index": 1,
        "tool": "file_write",
        "tool_input": {"path": path, "content": content},
        "status": "success",
        "result": {
            "ok": True,
            "data": None,
            "error": None,
            "metadata": {"bytes_written": len(content)},
        },
        "evaluation": {"passed": True},
    }


def mock_shallow_research_summary_step(summary: str) -> dict:
    """Mock a research summary that's suspiciously short."""
    return {
        "step_index": 0,
        "tool": "summarize_text",
        "tool_input": {"text": "dummy"},
        "status": "success",
        "result": {
            "ok": True,
            "data": {"summary": summary},
            "error": None,
            "metadata": {},
        },
        "evaluation": {"passed": True},
    }


# ============================================================================
# TEST CASES
# ============================================================================

TEST_CASES = {
    "1_structured_execution": [
        {
            "name": "Successful file write",
            "goal": "Create a file called test.txt with hello world",
            "steps": [mock_file_write_step("test.txt", "hello world")],
            "expect_semantic_pass": True,
            "expect_confidence": ">= 0.7",
        },
        {
            "name": "Empty file write (FAIL)",
            "goal": "Create a file called empty.txt",
            "steps": [mock_empty_file_write_step("empty.txt")],
            "expect_semantic_pass": False,
            "expect_confidence": "any",
        },
    ],
    "4_code_generation_quality": [
        {
            "name": "Real code generation",
            "goal": "Create a Python CLI todo app in todo_app.py",
            "steps": [
                mock_file_write_step(
                    "todo_app.py",
                    """import argparse
def main():
    parser = argparse.ArgumentParser()
    parser.add_argument('action')
    args = parser.parse_args()
    print(f"Action: {args.action}")

if __name__ == '__main__':
    main()
""",
                )
            ],
            "expect_semantic_pass": True,
            "expect_confidence": ">= 0.7",
        },
        {
            "name": "Placeholder code only (SHOULD FAIL)",
            "goal": "Create a Python app in placeholder_app.py",
            "steps": [mock_placeholder_file_write_step("placeholder_app.py")],
            "expect_semantic_pass": False,
            "expect_confidence": "< 0.65",
        },
    ],
    "3_calculation_grounding": [
        {
            "name": "Real calculation with output",
            "goal": "Calculate the sum of first 10 Fibonacci numbers",
            "steps": [mock_good_calculation_step("45")],
            "expect_semantic_pass": True,
            "expect_confidence": ">= 0.7",
        },
        {
            "name": "Calculation with no output (FAIL)",
            "goal": "Calculate something",
            "steps": [mock_empty_calculation_step()],
            "expect_semantic_pass": False,
            "expect_confidence": "< 0.65",
        },
        {
            "name": "Generated fake calculation (SHOULD CATCH)",
            "goal": "What is 2^20?",
            "steps": [mock_placeholder_file_write_step("result.txt")],  # No run_python!
            "expect_semantic_pass": False,
            "expect_confidence": "< 0.65",
        },
    ],
    "6_research_quality": [
        {
            "name": "Good research with structure",
            "goal": "Research and summarize machine learning",
            "steps": [
                mock_research_summary_file_step(
                    "ml_summary.md",
                    """# Machine Learning Summary

Machine learning is...

## Key Concepts
- Supervised learning
- Unsupervised learning

## Sources
- Wikipedia
- Coursera
""",
                )
            ],
            "expect_semantic_pass": True,
            "expect_confidence": ">= 0.65",
        },
        {
            "name": "Shallow research (no structure)",
            "goal": "Research Python",
            "steps": [
                mock_shallow_research_summary_step("Python is a programming language"),
            ],
            "expect_semantic_pass": False,
            "expect_confidence": "< 0.65",
        },
        {
            "name": "Research missing sources",
            "goal": "Research quantum computing",
            "steps": [
                mock_research_summary_file_step(
                    "quantum.md",
                    """# Quantum Computing

Quantum computing uses qubits...

## Key Concepts
- Superposition
- Entanglement
""",
                )
            ],
            "expect_semantic_pass": False,
            "expect_confidence": "< 0.7",
        },
    ],
    "7_semantic_verifier": [
        {
            "name": "Placeholder file (junk code)",
            "goal": "Create a file with placeholder code",
            "steps": [
                mock_file_write_step("junk.py", "pass"),
            ],
            "expect_semantic_pass": False,
            "expect_confidence": "any",  # May technically pass structurally
        },
        {
            "name": "Trivial calculation output",
            "goal": "Do a calculation",
            "steps": [
                mock_good_calculation_step("1"),  # Suspiciously small output
            ],
            "expect_semantic_pass": True,
            "expect_confidence": ">= 0.7",  # Should still pass
        },
    ],
    "15_semantic_vs_structural": [
        {
            "name": "Markdown headers but no content",
            "goal": "Research something",
            "steps": [
                mock_research_summary_file_step(
                    "fake_research.md",
                    """# Research Title
## Key Concepts
## Sources
""",
                )
            ],
            "expect_semantic_pass": False,
            "expect_confidence": "< 0.65",
            "notes": "Structurally valid but semantically empty",
        },
        {
            "name": "Fake calculation without execution",
            "goal": "Calculate Fibonacci",
            "steps": [
                mock_file_write_step("fib.py", "# The answer is 45"),  # No run_python!
            ],
            "expect_semantic_pass": False,
            "expect_confidence": "< 0.65",
            "notes": "File_write alone doesn't count as calculation",
        },
        {
            "name": "Claim of code but not really code",
            "goal": "Write code",
            "steps": [
                mock_file_write_step(
                    "code.py",
                    """# This is not real code
print('fake')
# TODO: implement actual functionality
""",
                )
            ],
            "expect_semantic_pass": False,
            "expect_confidence": "< 0.65",  # Should FAIL - weak code
            "notes": "Correctly rejects code lacking functions/classes/logic",
        },
    ],
}


class Runner:
    def __init__(self):
        self.results = {}
        self.failures = []

    def evaluate_condition(self, value: float, condition: str) -> bool:
        """Evaluate a condition like '>= 0.7' or 'any'."""
        if condition == "any":
            return True
        if ">=" in condition:
            threshold = float(condition.split(">= ")[1])
            return value >= threshold
        if "<" in condition:
            threshold = float(condition.split("< ")[1])
            return value < threshold
        return True

    def run_test(self, category: str, test: dict) -> dict:
        """Run a single test case."""
        name = test["name"]
        goal = test["goal"]
        steps = test["steps"]

        result = semantic_verify_goal(goal, steps)

        passed = result["passed"]
        confidence = result["confidence"]
        expected_pass = test["expect_semantic_pass"]
        expected_confidence = test["expect_confidence"]

        # Check expectations
        pass_matches = passed == expected_pass
        confidence_matches = self.evaluate_condition(confidence, expected_confidence)
        test_passed = pass_matches and confidence_matches

        return {
            "name": name,
            "goal": goal,
            "passed": test_passed,
            "result": result,
            "expected_pass": expected_pass,
            "expected_confidence": expected_confidence,
            "notes": test.get("notes", ""),
        }

    def run_all(self):
        """Run all test categories."""
        print("\n" + "=" * 80)
        print("JARVIS OFFLINE SEMANTIC VERIFICATION TEST SUITE")
        print("=" * 80)

        for category, tests in TEST_CASES.items():
            print(f"\n\n{category.upper()}")
            print("-" * 80)

            self.results[category] = []

            for test in tests:
                result = self.run_test(category, test)
                self.results[category].append(result)

                status = "✓ PASS" if result["passed"] else "❌ FAIL"
                print(f"\n{status}: {result['name']}")
                print(f"  Goal: {result['goal'][:60]}")
                print(f"  Semantic: {result['result']['passed']} (confidence: {result['result']['confidence']:.2f})")
                print(f"  Expected: {result['expected_pass']} (confidence: {result['expected_confidence']})")

                if result["result"]["issues"]:
                    print(f"  Issues: {result['result']['issues']}")

                if result["notes"]:
                    print(f"  📝 {result['notes']}")

                if not result["passed"]:
                    self.failures.append(result)

        self.print_summary()

    def print_summary(self):
        """Print final summary."""
        total_tests = sum(len(tests) for tests in self.results.values())
        total_passed = sum(
            len([r for r in tests if r["passed"]]) for tests in self.results.values()
        )

        print("\n\n" + "=" * 80)
        print("SUMMARY")
        print("=" * 80)
        print(f"\nTotal Tests: {total_tests}")
        print(f"Passed: {total_passed} ({100*total_passed/total_tests:.1f}%)")
        print(f"Failed: {len(self.failures)} ({100*len(self.failures)/total_tests:.1f}%)")

        if self.failures:
            print("\n\nFAILED TESTS:")
            for failure in self.failures:
                print(f"  ❌ {failure['name']}: {failure['goal'][:50]}")
                print(
                    f"     Expected {failure['expected_pass']}, got {failure['result']['passed']}"
                )


if __name__ == "__main__":
    runner = Runner()
    runner.run_all()
