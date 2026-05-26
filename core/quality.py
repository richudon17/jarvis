"""Authoritative quality gate for goal completion.

Final completion decision rule:
- `review_quality(...)[\"passed\"] == True` => goal may complete
- otherwise => goal must fail or replan
"""

from __future__ import annotations

import ast
import re
from pathlib import Path
from typing import Any

from core.workspace import resolve_workspace_path


CATEGORY_CODE = "code"
CATEGORY_RESEARCH = "research"
CATEGORY_CALCULATION = "calculation"
CATEGORY_FILE = "file"
CATEGORY_HYBRID = "hybrid"
CATEGORY_UNKNOWN = "unknown"


_PLACEHOLDER_RE = re.compile(
    r"\b(TODO|your code here|placeholder|not implemented|pass\s*$|pass\s*#|pass\n)\b",
    re.IGNORECASE | re.MULTILINE,
)
_FILENAME_PATTERN = re.compile(r"[\w./-]+\.[A-Za-z0-9]+")


def _goal_filenames(goal: str) -> list[str]:
    return [m.group(0).rstrip(".,;:") for m in _FILENAME_PATTERN.finditer(goal)]


def _goal_mentions(goal_lower: str, keywords: list[str]) -> bool:
    return any(k in goal_lower for k in keywords)


def _is_direct_file_overwrite_goal(goal_lower: str) -> bool:
    return (
        ("replace the contents of" in goal_lower or "replace contents of" in goal_lower or "overwrite" in goal_lower)
        and " with " in goal_lower
    )


def _successful_steps(steps: list[dict], tool: str | None = None) -> list[dict]:
    out = [s for s in steps if s.get("status") == "success"]
    if tool:
        return [s for s in out if s.get("tool") == tool]
    return out


def _resolve_path(path: str) -> Path:
    return resolve_workspace_path(path)


def _read_text(path: str) -> str:
    try:
        return _resolve_path(path).read_text(encoding="utf-8")
    except Exception:
        return ""


def _file_non_empty(path: str) -> tuple[bool, str]:
    p = _resolve_path(path)
    if not p.exists():
        return False, f"expected file {path} does not exist"
    if not p.is_file():
        return False, f"expected {path} to be a file"
    if p.stat().st_size <= 0:
        return False, f"expected {path} to be non-empty"
    return True, ""


def _failure_factor(factor: str, penalty: float, detail: str) -> dict[str, Any]:
    return {"factor": factor, "penalty": penalty, "detail": detail}


def _quality_result(
    *,
    passed: bool,
    score: float,
    issues: list[str],
    category: str,
    reasoning_trace: list[str],
    failure_factors: list[dict[str, Any]],
) -> dict[str, Any]:
    return {
        "passed": bool(passed),
        "score": float(max(0.0, min(1.0, score))),
        "issues": issues,
        "category": category,
        "reasoning_trace": reasoning_trace,
        "failure_factors": failure_factors,
    }


def _infer_category(goal: str, filenames: list[str], completed_steps: list[dict]) -> str:
    gl = goal.lower()

    if _goal_mentions(gl, ["research", "search", "look up", "find information", "investigate"]):
        return CATEGORY_RESEARCH

    if _goal_mentions(gl, ["calculate", "compute", "solve", "fibonacci", "sum", "average", "mean", "median"]):
        return CATEGORY_CALCULATION

    if filenames and _is_direct_file_overwrite_goal(gl):
        return CATEGORY_FILE

    if _goal_mentions(gl, ["reverse", "sort", "replace", "transform", "modify", "process", "convert"]):
        return CATEGORY_HYBRID

    if any(name.endswith(".py") for name in filenames):
        return CATEGORY_CODE

    if any(
        st.get("tool") == "file_write"
        and str((st.get("tool_input") or {}).get("path", "")).endswith(".py")
        for st in completed_steps
    ):
        return CATEGORY_CODE

    if filenames or any(st.get("tool") in {"file_write", "file_read"} for st in completed_steps):
        return CATEGORY_FILE

    return CATEGORY_UNKNOWN


def _review_python_code(goal: str, completed_steps: list[dict]) -> dict[str, Any]:
    reasoning: list[str] = ["Category=code: validating Python artifact quality."]
    factors: list[dict[str, Any]] = []
    issues: list[str] = []
    score = 1.0

    py_writes = [
        st for st in _successful_steps(completed_steps, "file_write")
        if str((st.get("tool_input") or {}).get("path", "")).endswith(".py")
    ]
    reasoning.append(f"Successful Python file_write steps: {len(py_writes)}.")
    if not py_writes:
        issues.append("No successful Python file_write evidence")
        factors.append(_failure_factor("missing_python_write", 1.0, issues[-1]))
        return _quality_result(
            passed=False,
            score=0.0,
            issues=issues,
            category=CATEGORY_CODE,
            reasoning_trace=reasoning,
            failure_factors=factors,
        )

    path = str((py_writes[-1].get("tool_input") or {}).get("path", ""))
    reasoning.append(f"Evaluating artifact: {path}.")
    ok, reason = _file_non_empty(path)
    if not ok:
        issues.append(reason)
        factors.append(_failure_factor("artifact_missing_or_empty", 1.0, reason))
        return _quality_result(
            passed=False,
            score=0.0,
            issues=issues,
            category=CATEGORY_CODE,
            reasoning_trace=reasoning,
            failure_factors=factors,
        )

    text = _read_text(path)
    reasoning.append(f"Artifact size characters: {len(text)}.")

    try:
        ast.parse(text)
        reasoning.append("AST parse succeeded.")
    except SyntaxError as e:
        issue = f"invalid python: {e.msg}"
        issues.append(issue)
        factors.append(_failure_factor("invalid_python_syntax", 1.0, issue))
        return _quality_result(
            passed=False,
            score=0.0,
            issues=issues,
            category=CATEGORY_CODE,
            reasoning_trace=reasoning,
            failure_factors=factors,
        )

    has_logic = any(token in text for token in ("def ", "class ", "for ", "while ", "return ", "import ", "print("))
    if not has_logic:
        issue = "No executable logic detected"
        issues.append(issue)
        factors.append(_failure_factor("low_logic_density", 0.4, issue))
        score -= 0.4
        reasoning.append("Penalty applied: no executable logic markers found.")
    else:
        reasoning.append("Executable logic markers found.")

    if _PLACEHOLDER_RE.search(text):
        issue = "Contains placeholder/stub logic"
        issues.append(issue)
        factors.append(_failure_factor("placeholder_logic", 0.5, issue))
        score -= 0.5
        reasoning.append("Penalty applied: placeholder/stub markers detected.")
    else:
        reasoning.append("No placeholder/stub markers detected.")

    passed = score >= 0.6 and not issues
    reasoning.append(f"Final code score={max(0.0, score):.2f}, passed={passed}.")
    return _quality_result(
        passed=passed,
        score=max(0.0, score),
        issues=issues,
        category=CATEGORY_CODE,
        reasoning_trace=reasoning,
        failure_factors=factors,
    )


def _review_research(goal: str, completed_steps: list[dict]) -> dict[str, Any]:
    reasoning: list[str] = ["Category=research: validating search and summary/report evidence."]
    factors: list[dict[str, Any]] = []
    issues: list[str] = []

    successful_searches = _successful_steps(completed_steps, "web_search")
    reasoning.append(f"Successful web_search steps: {len(successful_searches)}.")
    if not successful_searches:
        issue = "No successful web_search evidence"
        issues.append(issue)
        factors.append(_failure_factor("missing_research_source", 1.0, issue))
        return _quality_result(
            passed=False,
            score=0.0,
            issues=issues,
            category=CATEGORY_RESEARCH,
            reasoning_trace=reasoning,
            failure_factors=factors,
        )

    successful_summary = _successful_steps(completed_steps, "summarize_text")
    reasoning.append(f"Successful summarize_text steps: {len(successful_summary)}.")
    if successful_summary:
        summary = ((successful_summary[-1].get("result") or {}).get("data") or {}).get("summary", "")
        summary_len = len(summary.strip()) if isinstance(summary, str) else 0
        reasoning.append(f"Summary length: {summary_len}.")
        if isinstance(summary, str) and summary_len >= 40:
            reasoning.append("Summary threshold met.")
            return _quality_result(
                passed=True,
                score=0.85,
                issues=[],
                category=CATEGORY_RESEARCH,
                reasoning_trace=reasoning,
                failure_factors=[],
            )
        issue = "Summary content is too short or empty"
        issues.append(issue)
        factors.append(_failure_factor("weak_summary", 0.8, issue))
        return _quality_result(
            passed=False,
            score=0.2,
            issues=issues,
            category=CATEGORY_RESEARCH,
            reasoning_trace=reasoning,
            failure_factors=factors,
        )

    report_writes = [
        st for st in _successful_steps(completed_steps, "file_write")
        if not str((st.get("tool_input") or {}).get("path", "")).endswith(".py")
    ]
    reasoning.append(f"Successful non-Python report writes: {len(report_writes)}.")
    if not report_writes:
        issue = "Research goal missing summarize_text or saved report artifact"
        issues.append(issue)
        factors.append(_failure_factor("missing_research_artifact", 0.8, issue))
        return _quality_result(
            passed=False,
            score=0.2,
            issues=issues,
            category=CATEGORY_RESEARCH,
            reasoning_trace=reasoning,
            failure_factors=factors,
        )

    report_path = str((report_writes[-1].get("tool_input") or {}).get("path", ""))
    reasoning.append(f"Evaluating report artifact: {report_path}.")
    ok, reason = _file_non_empty(report_path)
    if not ok:
        issues.append(reason)
        factors.append(_failure_factor("report_missing_or_empty", 0.9, reason))
        return _quality_result(
            passed=False,
            score=0.1,
            issues=issues,
            category=CATEGORY_RESEARCH,
            reasoning_trace=reasoning,
            failure_factors=factors,
        )

    report_text = _read_text(report_path).lower()
    has_sources = ("sources" in report_text) or ("http" in report_text)
    reasoning.append(f"Report source markers present: {has_sources}.")
    if not has_sources:
        issue = "Research report missing source evidence"
        issues.append(issue)
        factors.append(_failure_factor("missing_source_markers", 0.45, issue))
        return _quality_result(
            passed=False,
            score=0.35,
            issues=issues,
            category=CATEGORY_RESEARCH,
            reasoning_trace=reasoning,
            failure_factors=factors,
        )

    reasoning.append("Research report validated.")
    return _quality_result(
        passed=True,
        score=0.78,
        issues=[],
        category=CATEGORY_RESEARCH,
        reasoning_trace=reasoning,
        failure_factors=[],
    )


def _review_calculation(completed_steps: list[dict]) -> dict[str, Any]:
    reasoning: list[str] = ["Category=calculation: validating executable numeric output."]
    factors: list[dict[str, Any]] = []
    issues: list[str] = []

    run_steps = _successful_steps(completed_steps, "run_python")
    reasoning.append(f"Successful run_python steps: {len(run_steps)}.")
    if not run_steps:
        issue = "No successful run_python evidence"
        issues.append(issue)
        factors.append(_failure_factor("missing_runtime_calculation", 1.0, issue))
        return _quality_result(
            passed=False,
            score=0.0,
            issues=issues,
            category=CATEGORY_CALCULATION,
            reasoning_trace=reasoning,
            failure_factors=factors,
        )

    stdout = ((run_steps[-1].get("result") or {}).get("data") or {}).get("stdout", "")
    stdout_len = len(stdout.strip()) if isinstance(stdout, str) else 0
    reasoning.append(f"run_python stdout length: {stdout_len}.")
    if not isinstance(stdout, str) or not stdout.strip():
        issue = "run_python produced no meaningful stdout"
        issues.append(issue)
        factors.append(_failure_factor("empty_calculation_output", 0.8, issue))
        return _quality_result(
            passed=False,
            score=0.2,
            issues=issues,
            category=CATEGORY_CALCULATION,
            reasoning_trace=reasoning,
            failure_factors=factors,
        )

    reasoning.append("Calculation output validated.")
    return _quality_result(
        passed=True,
        score=0.82,
        issues=[],
        category=CATEGORY_CALCULATION,
        reasoning_trace=reasoning,
        failure_factors=[],
    )


def _review_hybrid(completed_steps: list[dict]) -> dict[str, Any]:
    reasoning: list[str] = ["Category=hybrid: validating read-transform-write flow."]
    factors: list[dict[str, Any]] = []
    issues: list[str] = []

    has_read = bool(_successful_steps(completed_steps, "file_read"))
    writes = _successful_steps(completed_steps, "file_write")
    reasoning.append(f"Successful file_read present: {has_read}.")
    reasoning.append(f"Successful file_write count: {len(writes)}.")
    if not has_read or not writes:
        issue = "Hybrid task requires successful file_read and file_write"
        issues.append(issue)
        factors.append(_failure_factor("incomplete_hybrid_pipeline", 1.0, issue))
        return _quality_result(
            passed=False,
            score=0.0,
            issues=issues,
            category=CATEGORY_HYBRID,
            reasoning_trace=reasoning,
            failure_factors=factors,
        )

    out_path = str((writes[-1].get("tool_input") or {}).get("path", ""))
    reasoning.append(f"Validating hybrid output artifact: {out_path}.")
    ok, reason = _file_non_empty(out_path)
    if not ok:
        issues.append(reason)
        factors.append(_failure_factor("hybrid_output_invalid", 0.9, reason))
        return _quality_result(
            passed=False,
            score=0.1,
            issues=issues,
            category=CATEGORY_HYBRID,
            reasoning_trace=reasoning,
            failure_factors=factors,
        )

    reasoning.append("Hybrid pipeline output validated.")
    return _quality_result(
        passed=True,
        score=0.8,
        issues=[],
        category=CATEGORY_HYBRID,
        reasoning_trace=reasoning,
        failure_factors=[],
    )


def _review_file(completed_steps: list[dict]) -> dict[str, Any]:
    reasoning: list[str] = ["Category=file: validating direct file evidence."]
    factors: list[dict[str, Any]] = []
    issues: list[str] = []

    writes = _successful_steps(completed_steps, "file_write")
    reads = _successful_steps(completed_steps, "file_read")
    reasoning.append(f"Successful file_write count: {len(writes)}.")
    reasoning.append(f"Successful file_read count: {len(reads)}.")

    if writes:
        path = str((writes[-1].get("tool_input") or {}).get("path", ""))
        reasoning.append(f"Validating written artifact: {path}.")
        ok, reason = _file_non_empty(path)
        if not ok:
            issues.append(reason)
            factors.append(_failure_factor("written_artifact_invalid", 1.0, reason))
            return _quality_result(
                passed=False,
                score=0.0,
                issues=issues,
                category=CATEGORY_FILE,
                reasoning_trace=reasoning,
                failure_factors=factors,
            )
        reasoning.append("Written artifact validated.")
        return _quality_result(
            passed=True,
            score=0.78,
            issues=[],
            category=CATEGORY_FILE,
            reasoning_trace=reasoning,
            failure_factors=[],
        )

    if reads:
        content = (((reads[-1].get("result") or {}).get("data") or {}).get("content") or "")
        content_len = len(content.strip()) if isinstance(content, str) else 0
        reasoning.append(f"Read content length: {content_len}.")
        if isinstance(content, str) and content.strip():
            reasoning.append("Read evidence is non-empty.")
            return _quality_result(
                passed=True,
                score=0.72,
                issues=[],
                category=CATEGORY_FILE,
                reasoning_trace=reasoning,
                failure_factors=[],
            )

    issue = "No meaningful file evidence"
    issues.append(issue)
    factors.append(_failure_factor("missing_file_evidence", 1.0, issue))
    return _quality_result(
        passed=False,
        score=0.0,
        issues=issues,
        category=CATEGORY_FILE,
        reasoning_trace=reasoning,
        failure_factors=factors,
    )


def review_quality(
    goal: str,
    completed_steps: list[dict],
    attempted_steps: list[dict] | None = None,
) -> dict[str, Any]:
    """Only authoritative final quality decision point."""
    attempted_steps = attempted_steps or completed_steps
    filenames = _goal_filenames(goal)
    category = _infer_category(goal, filenames, completed_steps)

    if category == CATEGORY_CODE:
        return _review_python_code(goal, completed_steps)
    if category == CATEGORY_RESEARCH:
        return _review_research(goal, completed_steps)
    if category == CATEGORY_CALCULATION:
        return _review_calculation(completed_steps)
    if category == CATEGORY_HYBRID:
        return _review_hybrid(completed_steps)
    if category == CATEGORY_FILE:
        return _review_file(completed_steps)

    return _quality_result(
        passed=False,
        score=0.0,
        issues=["Unknown goal category; insufficient evidence for completion"],
        category=CATEGORY_UNKNOWN,
        reasoning_trace=["Category=unknown: cannot establish reliable completion evidence."],
        failure_factors=[_failure_factor("unknown_category", 1.0, "Goal classification failed")],
    )
