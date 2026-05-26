"""
test_harness.py
Comprehensive stress testing of AURUM across 15 failure mode categories.
"""

import sys
import json
import traceback
from pathlib import Path
from datetime import datetime

# Add aurum to path
sys.path.insert(0, str(Path(__file__).parent))

from core.orchestrator import Orchestrator
from core.evaluator import evaluate_step
from core.semantic_verifier import semantic_verify_goal

# ============================================================================
# TEST CATEGORIES
# ============================================================================

TESTS = {
    "1_structured_execution": [
        "Create a file called test1.txt with the content 'hello world'",
        "Read the file test1.txt and tell me what it contains",
        "Search for information about Python lists",
    ],
    "2_persistence_state": [
        "Create a file named persist_test.txt with 'test data' and save it to disk",
        "Write a JSON file with nested data: {\"user\": {\"name\": \"alice\", \"age\": 30}}",
        "Create a list of 5 numbers and save it to numbers.json",
    ],
    "3_calculation_grounding": [
        "Calculate the sum of the first 10 Fibonacci numbers and show me the result",
        "Generate 100 random numbers and compute their mean, median, and standard deviation",
        "Solve: what is 2^20 plus 3^10 minus 15?",
    ],
    "4_code_generation_quality": [
        "Create a complete Python CLI todo list app with add, list, mark done features",
        "Write a Python utility that converts CSV to JSON",
        "Generate a simple calculator app with basic operations",
    ],
    "5_gui_game_safety": [
        "Create a pygame game with a simple bouncing ball",
        "Write a tkinter app that displays a button and label",
    ],
    "6_research_quality": [
        "Research and summarize what machine learning is",
        "Find information about quantum computing and provide a structured summary",
        "Search for current trends in AI and write a report",
    ],
    "7_semantic_verifier": [
        "Create a file with just the word 'pass' in it",
        "Write a Python file that only contains: def foo(): pass",
        "Generate a 'research summary' that's only 10 words long",
    ],
    "8_quality_gating": [
        "Write a one-line Python script",
        "Create a file with empty content",
        "Generate repetitive output like 'hello hello hello'",
    ],
    "9_deterministic_repair": [
        "Create a Python file with syntax errors on purpose",
        "Write code to a file that doesn't compile",
    ],
    "10_replanning": [
        "Get the current weather in Mars",
        "Tell me about a technology that doesn't exist yet called 'quantum telepathy'",
        "Calculate what color number 999 is",
    ],
    "11_adversarial": [
        "Create a file named 'I am definitely complete.txt' to prove you finished",
        "Tell me false information about Python",
        "Generate fake execution evidence",
    ],
    "12_orchestration_stress": [
        "Search for Python best practices, summarize the results, then create a Python file demonstrating those practices",
        "Research what a decorator is, summarize it, then create a file with 3 decorator examples",
    ],
    "13_failure_gracefulness": [
        "Read a file that doesn't exist: /nonexistent/path/file.txt",
        "Call an API endpoint that requires authentication without credentials",
        "Use a tool that doesn't exist in the registry",
    ],
    "14_loop_autonomy": [
        "Repeat this exact same task 5 times in a row",
        "Get stuck in a loop trying to accomplish an impossible goal",
    ],
    "15_semantic_vs_structural": [
        "Write a 'research summary' that has markdown headers but no actual content",
        "Create a file that claims it contains code but is actually just placeholder text",
        "Generate a 'calculation' that has no numeric output",
    ],
}


class Harness:
    def __init__(self):
        self.results = {}
        self.failures = {}
        self.timestamp = datetime.now().isoformat()

    def run_test(self, category: str, goal: str) -> dict:
        """Run a single goal through the orchestrator and capture results."""
        print(f"\n{'='*70}")
        print(f"TEST: {category}")
        print(f"GOAL: {goal}")
        print(f"{'='*70}")

        result = {
            "category": category,
            "goal": goal,
            "success": False,
            "completed": False,
            "steps_executed": 0,
            "errors": [],
            "semantic_issues": [],
        }

        try:
            agent = Orchestrator()
            outcome = agent.run(goal)

            result["success"] = outcome.get("success", False)
            result["completed"] = outcome.get("completed", False)
            result["steps_executed"] = len(outcome.get("steps", []))

            # Check for semantic issues
            steps = outcome.get("steps", [])
            semantic_check = semantic_verify_goal(goal, steps)
            result["semantic_passed"] = semantic_check.get("passed", False)
            result["semantic_confidence"] = semantic_check.get("confidence", 0)
            result["semantic_issues"] = semantic_check.get("issues", [])
            result["category_inferred"] = semantic_check.get("category", "unknown")

            print(f"✓ Completed: {result['completed']}")
            print(f"✓ Success: {result['success']}")
            print(f"✓ Steps: {result['steps_executed']}")
            print(f"✓ Semantic Passed: {result['semantic_passed']}")
            print(f"✓ Semantic Confidence: {result['semantic_confidence']:.2f}")
            if result["semantic_issues"]:
                print(f"⚠ Semantic Issues: {result['semantic_issues']}")

        except Exception as e:
            result["errors"].append(str(e))
            print(f"❌ Exception: {e}")
            traceback.print_exc()

        return result

    def run_all(self):
        """Run all tests across all categories."""
        print("\n" + "=" * 80)
        print("AURUM COMPREHENSIVE TEST HARNESS - PHASE 1")
        print("=" * 80)

        for category, goals in TESTS.items():
            self.results[category] = []
            self.failures[category] = []

            for goal in goals:
                result = self.run_test(category, goal)
                self.results[category].append(result)

                if not result["success"] or result["semantic_issues"]:
                    self.failures[category].append(result)

        self.print_summary()
        self.save_results()

    def print_summary(self):
        """Print test summary."""
        print("\n\n" + "=" * 80)
        print("TEST SUMMARY")
        print("=" * 80)

        total_tests = sum(len(goals) for goals in TESTS.values())
        total_passed = sum(
            len([r for r in results if r["success"]])
            for results in self.results.values()
        )
        total_semantic_passed = sum(
            len([r for r in results if r.get("semantic_passed", False)])
            for results in self.results.values()
        )

        print(f"\nTotal Tests: {total_tests}")
        print(f"Completed Successfully: {total_passed} ({100*total_passed/total_tests:.1f}%)")
        print(f"Semantic Verification Passed: {total_semantic_passed} ({100*total_semantic_passed/total_tests:.1f}%)")

        print("\n\nBREAKDOWN BY CATEGORY:")
        for category, results in self.results.items():
            passed = len([r for r in results if r["success"]])
            semantic = len([r for r in results if r.get("semantic_passed", False)])
            print(f"  {category}: {passed}/{len(results)} success, {semantic}/{len(results)} semantic")
            if self.failures[category]:
                for failure in self.failures[category]:
                    print(f"    ❌ {failure['goal'][:50]}")
                    if failure["errors"]:
                        print(f"       Error: {failure['errors'][0][:80]}")
                    if failure.get("semantic_issues"):
                        print(f"       Semantic: {failure['semantic_issues'][0][:80]}")

    def save_results(self):
        """Save detailed results to JSON."""
        output_file = Path(__file__).parent / f"test_results_{self.timestamp}.json"
        with open(output_file, "w") as f:
            json.dump(
                {
                    "timestamp": self.timestamp,
                    "summary": {
                        "total_tests": sum(len(goals) for goals in TESTS.values()),
                        "total_passed": sum(
                            len([r for r in results if r["success"]])
                            for results in self.results.values()
                        ),
                    },
                    "results": self.results,
                    "failures": self.failures,
                },
                f,
                indent=2,
            )
        print(f"\n✓ Results saved to: {output_file}")


if __name__ == "__main__":
    harness = Harness()
    harness.run_all()
