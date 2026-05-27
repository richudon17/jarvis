"""Advisory goal verification layer.

This module is non-authoritative by design:
- It generates goal-completion hints from executed evidence.
- It does NOT decide final success/failure.
- `core/quality.py` is the only final decision authority.
"""

from __future__ import annotations

import re
from pathlib import Path
import ast
from pathlib import Path


FILENAME_PATTERN = re.compile(r"[\w./-]+\.[A-Za-z0-9]+")


def _goal_filenames(goal: str) -> list[str]:
    return [m.group(0).rstrip(".,;:") for m in FILENAME_PATTERN.finditer(goal)]


def _goal_mentions_any(goal_lower: str, keywords: list[str]) -> bool:
    return any(keyword in goal_lower for keyword in keywords)


def _path_exists_with_content(path: str) -> bool:
    file_path = Path(path).expanduser()
    return file_path.exists() and file_path.is_file() and file_path.stat().st_size > 0


def _successful_steps(completed_steps: list[dict], tool: str | None = None) -> list[dict]:
    steps = [s for s in completed_steps if s.get("status") in {"success", "done"}]
    if tool:
        return [s for s in steps if s.get("tool") == tool]
    return steps


def verify_goal(
    goal: str,
    completed_steps: list[dict],
    attempted_steps: list[dict] | None = None,
) -> dict:
    """Return advisory hints only; never authoritative pass/fail."""
    attempted_steps = attempted_steps or []
    goal_lower = goal.lower()
    filenames = _goal_filenames(goal)
    hints: list[str] = []
    evidence: list[str] = []

    successful_steps = _successful_steps(completed_steps)
    if not successful_steps:
        hints.append("No successful steps were recorded before completion.")

    if _goal_mentions_any(goal_lower, ["research", "search", "look up", "find information"]):
        if not _successful_steps(completed_steps, "web_search"):
            hints.append("Research goal has no successful web_search evidence.")
        else:
            evidence.append("web_search step present")

    if _goal_mentions_any(goal_lower, ["calculate", "compute", "solve"]) and not filenames:
        run_steps = _successful_steps(completed_steps, "run_python")
        if not run_steps:
            hints.append("Calculation goal has no successful run_python step.")
        else:
            has_stdout = any(
                isinstance((st.get("result") or {}).get("data", {}).get("stdout", ""), str)
                and bool((st.get("result") or {}).get("data", {}).get("stdout", "").strip())
                for st in run_steps
            )
            if not has_stdout:
                hints.append("Calculation goal run_python evidence has empty stdout.")
            else:
                evidence.append("run_python stdout present")

    for path in filenames:
        if _path_exists_with_content(path):
            evidence.append(f"artifact exists: {path}")
        else:
            hints.append(f"expected artifact missing/empty: {path}")

    # Check smoke_test metadata and Python syntax
    confidence_cap = 1.0
    for step in _successful_steps(completed_steps, "file_write"):
        tool_input = step.get("tool_input") or {}
        metadata = ((step.get("result") or {}).get("metadata") or {})
        smoke = metadata.get("smoke_test")
        path = str(tool_input.get("path", ""))

        if smoke:
            compiled = smoke.get("compiled")
            executed = smoke.get("executed")
            execution_skipped = smoke.get("execution_skipped", False)
            if compiled is False:
                hints.append(f"smoke_test reports compile failure for {path}")
            elif compiled is True and not executed and not execution_skipped:
                confidence_cap = min(confidence_cap, 0.65)

        if path.endswith(".py") and Path(path).exists():
            try:
                content = Path(path).read_text(encoding="utf-8")
                ast.parse(content)
            except SyntaxError:
                hints.append(f"Python syntax error in {path}")

    confidence = 0.85 if not hints else 0.45
    if not successful_steps:
        confidence = 0.25
    confidence = min(confidence, confidence_cap)

    return {
        "passed": not hints,
        "advisory_status": "ok" if not hints else "warn",
        "confidence": confidence,
        "hints": hints,
        "evidence": evidence,
        "successful_steps": len(successful_steps),
        "attempted_steps": len(attempted_steps),
}