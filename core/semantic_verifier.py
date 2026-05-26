"""Advisory semantic analysis layer.

This module provides optional semantic hints and confidence scores.
It does not produce authoritative pass/fail decisions.
"""

from __future__ import annotations

from typing import Any
import re


def _infer_category_from_goal(goal: str) -> str:
    gl = goal.lower()
    if any(k in gl for k in ["research", "search", "look up", "find information", "investigate"]):
        return "research"
    if any(k in gl for k in ["calculate", "compute", "solve", "fibonacci", "sum ", "mean", "median", "average"]):
        return "calculation"
    if re.search(r"[\w./-]+\.py\b", goal):
        return "code"
    if any(k in gl for k in ["create a file", "write to", "save to", "write a file", ".txt", ".md", ".json"]):
        return "file"
    return "unknown"


def _is_trivial_placeholder_code(content: str) -> bool:
    trimmed = content.strip().lower()
    if not trimmed:
        return True
    markers = ["todo", "placeholder", "not implemented", "\npass", "\n..."]
    return any(marker in trimmed for marker in markers)


def semantic_verify_goal(
    goal: str,
    completed_steps: list[dict],
    attempted_steps: list[dict] | None = None,
) -> dict[str, Any]:
    """Return non-authoritative semantic hints."""
    attempted_steps = attempted_steps or []
    category = _infer_category_from_goal(goal)
    hints: list[str] = []
    evidence: list[str] = []
    confidence = 0.55

    if category == "research":
        has_summary = any(st.get("tool") == "summarize_text" for st in completed_steps)
        if not has_summary:
            hints.append("Research goal lacks summarize_text evidence.")
            confidence = 0.35
        else:
            evidence.append("summarize_text step present")
            confidence = 0.75

    elif category == "calculation":
        run_steps = [st for st in completed_steps if st.get("tool") == "run_python" and st.get("status") == "success"]
        if not run_steps:
            hints.append("Calculation goal has no successful run_python step.")
            confidence = 0.3
        else:
            stdout = ((run_steps[-1].get("result") or {}).get("data") or {}).get("stdout", "")
            if not isinstance(stdout, str) or not stdout.strip():
                hints.append("Calculation output appears empty.")
                confidence = 0.35
            else:
                evidence.append("calculation stdout present")
                confidence = 0.8

    elif category == "code":
        py_writes = [
            st for st in completed_steps
            if st.get("tool") == "file_write"
            and str((st.get("tool_input") or {}).get("path", "")).endswith(".py")
            and st.get("status") == "success"
        ]
        if not py_writes:
            hints.append("Code goal has no successful Python file write evidence.")
            confidence = 0.3
        else:
            content = str((py_writes[-1].get("tool_input") or {}).get("content", ""))
            if _is_trivial_placeholder_code(content):
                hints.append("Python artifact looks like placeholder/stub content.")
                confidence = 0.35
            else:
                evidence.append("python artifact content appears substantive")
                confidence = 0.78

    elif category == "file":
        file_writes = [st for st in completed_steps if st.get("tool") == "file_write" and st.get("status") == "success"]
        if not file_writes:
            hints.append("File goal has no successful file_write step.")
            confidence = 0.3
        else:
            evidence.append("file_write step present")
            confidence = 0.72

    else:
        hints.append("Could not infer semantic category from goal text.")
        confidence = 0.4

    return {
        "advisory_status": "ok" if not hints else "warn",
        "confidence": confidence,
        "hints": hints,
        "evidence": evidence,
        "category": category,
        "attempted_steps": len(attempted_steps),
    }
