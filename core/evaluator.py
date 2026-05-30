"""Observation layer for step execution.

This module is intentionally non-authoritative:
- It describes what happened in a tool call.
- It extracts structured observations and issues.
- It does NOT decide pass/fail for goal completion.
"""

from __future__ import annotations

import json


def _normalize_result(tool_result) -> dict:
    if isinstance(tool_result, dict):
        return {
            "ok": bool(tool_result.get("ok", False)),
            "data": tool_result.get("data"),
            "error": tool_result.get("error"),
            "metadata": tool_result.get("metadata") or {},
        }
    return {
        "ok": bool(tool_result),
        "data": None,
        "error": "tool produced non-structured result",
        "metadata": {"legacy": True},
    }


def _summarize_output(tool: str, data) -> str:
    if data is None:
        return f"{tool}: no output data"
    if isinstance(data, dict):
        if "stdout" in data:
            stdout = str(data.get("stdout") or "").strip()
            return f"{tool}: stdout_len={len(stdout)}"
        if "content" in data:
            content = str(data.get("content") or "").strip()
            return f"{tool}: content_len={len(content)}"
        if "entries" in data and isinstance(data.get("entries"), list):
            return f"{tool}: entries={len(data.get('entries', []))}"
        if "summary" in data:
            summary = str(data.get("summary") or "").strip()
            return f"{tool}: summary_len={len(summary)}"
        return f"{tool}: keys={sorted(data.keys())}"
    return f"{tool}: output_type={type(data).__name__}"


def evaluate_step(step: dict) -> dict:
    """Attach structured observations to a completed step."""
    status = step.get("status", "unknown")
    tool = step.get("tool", "")
    normalized = _normalize_result(step.get("result"))

    data = normalized.get("data")
    metadata = normalized.get("metadata") or {}
    issues: list[str] = []
    detected_errors: list[str] = []
    anomalies: list[str] = []

    if not normalized.get("ok", False):
        err = normalized.get("error") or "tool reported failure"
        issues.append(err)
        detected_errors.append(str(err))

    if tool == "file_write":
        bw = metadata.get("bytes_written")
        if isinstance(bw, int) and bw <= 0:
            issue = "file_write produced empty file"
            issues.append(issue)
            anomalies.append(issue)

    if tool == "file_read":
        content = (data or {}).get("content")
        if isinstance(content, str) and not content.strip():
            issue = "file_read returned empty content"
            issues.append(issue)
            anomalies.append(issue)

    if tool == "run_python":
        exit_code = metadata.get("exit_code")
        if exit_code is not None and exit_code != 0:
            issue = f"run_python nonzero exit_code={exit_code}"
            issues.append(issue)
            detected_errors.append(issue)

    if tool in ("web_search", "summarize_text") and data is None:
        issue = "tool returned no data"
        issues.append(issue)
        anomalies.append(issue)

    observation = {
        "tool": tool,
        "status": status,
        "ok": bool(normalized.get("ok", False)),
        "error": normalized.get("error"),
        "issues": issues,
        "detected_errors": detected_errors,
        "anomalies": anomalies,
        "output_summary": _summarize_output(tool, data),
        "has_data": data is not None,
    }

    return {
        **step,
        "result": normalized,
        "observation": observation,
        "evaluation": {
            "passed": tool == "done" or (normalized.get("ok", False) and not issues),
            "issues": issues,
        },
    }


def check_loop_detection(step_history: list, current_step: dict, threshold: int = 3) -> bool:
    """Detect if we're stuck in a loop — same tool + same input repeated too many times."""
    tool = current_step.get("tool")
    current_input = current_step.get("tool_input", {})
    # Sort dict keys so {"a":1,"b":2} and {"b":2,"a":1} are treated identically
    normalized_input = json.dumps(current_input, sort_keys=True) if isinstance(current_input, dict) else str(current_input)

    count = sum(
        1
        for s in step_history
        if s.get("tool") == tool and (
            json.dumps(s.get("tool_input", {}), sort_keys=True)
            if isinstance(s.get("tool_input"), dict)
            else str(s.get("tool_input", {}))
        ) == normalized_input
    )
    return count >= threshold